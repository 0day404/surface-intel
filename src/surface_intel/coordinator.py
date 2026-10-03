from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from .adapters.base import TaskContext
from .adapters.mock import MockFofaAdapter, MockPassiveAdapter
from .adapters.capabilities import CapabilityRegistry
from .execution import AdapterExecutor, ExecutionContext
from .normalization import normalize_target
from .state.database import Database
from .state.actions import ActionRepository
from .state.recovery import create_checkpoint, latest_checkpoint
from .states import ActionStatus, TaskStatus

MODES = ("serial", "delegated", "hybrid")
DEFAULT_CONCURRENCY = 2


async def run_offline_task(
    db: Database,
    task_id: str,
    fofa_pages: list[dict] | None = None,
    *,
    mode: str = "serial",
    max_consecutive_errors: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict[str, int | str]:
    """Offline orchestration loop with real scheduling semantics (plan R5).

    `mode` maps to an actual strategy: `serial` executes planned actions in
    order; `hybrid` runs passive actions under a concurrency semaphore; the
    `delegated` mode partitions actions per adapter into lease-backed subtask
    rows before executing each group. Stop tokens: a paused/cancelled task or
    the consecutive-error threshold stops scheduling between actions - the
    executor refuses anything already in flight from a stopped context.
    """
    if mode not in MODES:
        raise ValueError(f"invalid_mode:{mode}")
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (task_id,)), None)
    if task is None:
        raise ValueError("task_not_found")
    scope_row = next(db.rows("SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY created_at DESC", (task_id,)), None)
    if scope_row is None or not scope_row["confirmed"]:
        raise ValueError("scope_not_confirmed")
    context = TaskContext(
        task_id,
        scope_row["scope_version"],
        {
            "scope_confirmed": bool(scope_row["confirmed"]),
            "active_level": "passive_only",
        },
    )
    passive = MockPassiveAdapter()
    fofa = MockFofaAdapter(fofa_pages or [{"observations": [], "has_more": False}])
    registry = CapabilityRegistry([passive.manifest, fofa.manifest])
    executor = AdapterExecutor(db, registry)
    action_repo = ActionRepository(db)
    execution_context = ExecutionContext(
        task_id=task_id,
        scope_version=scope_row["scope_version"],
        scope_confirmed=True,
        active_level="passive_only",
        max_consecutive_errors=max_consecutive_errors,
        metadata={
            "authorized_domains": json.loads(scope_row["payload_json"]).get("authorized_domains", []),
            "scope_payload": json.loads(scope_row["payload_json"]),
        },
    )
    observations = 0
    assets = 0
    if task["status"] in {TaskStatus.PAUSED.value, TaskStatus.CANCELLED.value}:
        raise ValueError(f"task_{task['status']}")
    if task["status"] == TaskStatus.CORRELATING.value:
        return {"task_id": task_id, "observations": 0, "assets": 0, "status": "already_completed"}
    with db.connect() as connection:
        connection.execute("UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?", (TaskStatus.COLLECTING_PASSIVE.value, task_id))
    actions = list(db.rows("SELECT * FROM action WHERE task_id=? AND status=?", (task_id, ActionStatus.PLANNED.value)))

    def _stop_reason() -> str | None:
        row = next(db.rows("SELECT status FROM task WHERE task_id=?", (task_id,)), None)
        if row is None:
            return None
        if row["status"] == TaskStatus.PAUSED.value:
            return "paused"
        if row["status"] == TaskStatus.CANCELLED.value:
            return "cancelled"
        return None

    async def _execute_action(action) -> None:
        nonlocal observations
        if not action_repo.claim(action["action_id"]):
            return  # lost the race; another worker owns it
        action_input = json.loads(action["input_json"]) if action["input_json"] else {
            "target": action["idempotency_key"],
            "capability": "passive_observation",
        }
        action_input["action_id"] = action["action_id"]
        result = await executor.execute(passive, action_input, execution_context)
        with db.connect() as connection:
            for item in result.observations:
                _store_observation(connection, task_id, result, item)
                observations += 1
        if result.status == ActionStatus.COMPLETED.value:
            action_repo.complete(action["action_id"], result.request_id, request_id=result.request_id,
                                 cost_units=result.cost_units)
        elif result.status in {ActionStatus.PARTIAL.value, ActionStatus.FAILED.value, ActionStatus.TIMED_OUT.value}:
            action_repo.fail(action["action_id"], result.error or result.status, partial=result.status == ActionStatus.PARTIAL.value)
        elif result.status == ActionStatus.BLOCKED.value:
            with db.connect() as connection:
                connection.execute(
                    "UPDATE action SET status=?, error_code=?, last_error_code=?, completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE action_id=?",
                    (ActionStatus.BLOCKED.value, result.error, result.error, action["action_id"]),
                )
        create_checkpoint(
            db,
            task_id,
            phase="collecting_passive",
            payload={"last_action_id": action["action_id"], "observations": observations, "assets": assets},
        )

    stop_reason: str | None = None
    if mode == "hybrid":
        semaphore = asyncio.Semaphore(max(1, concurrency))
        in_flight = {"count": 0, "max": 0}

        async def _run_under_semaphore(action) -> None:
            nonlocal stop_reason
            reason = _stop_reason() or (
                "consecutive_errors" if execution_context.stop_requested else None)
            if reason:
                stop_reason = stop_reason or reason
                return
            async with semaphore:
                in_flight["count"] += 1
                in_flight["max"] = max(in_flight["max"], in_flight["count"])
                try:
                    await _execute_action(action)
                finally:
                    in_flight["count"] -= 1

        await asyncio.gather(*(_run_under_semaphore(action) for action in actions))
    elif mode == "delegated":
        groups: dict[str, list] = {}
        for action in actions:
            groups.setdefault(action["adapter"], []).append(action)
        for adapter_name, group in groups.items():
            subtask_id = _ensure_delegated_subtask(db, task_id, adapter_name, scope_row["scope_version"])
            with db.connect() as connection:
                connection.execute(
                    "UPDATE subtask SET status='started', started_at=CURRENT_TIMESTAMP, last_heartbeat_at=CURRENT_TIMESTAMP WHERE subtask_id=?",
                    (subtask_id,),
                )
            for action in group:
                reason = _stop_reason() or (
                    "consecutive_errors" if execution_context.stop_requested else None)
                if reason:
                    stop_reason = stop_reason or reason
                    break
                await _execute_action(action)
            with db.connect() as connection:
                connection.execute(
                    "UPDATE subtask SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE subtask_id=?",
                    (subtask_id,),
                )
    else:
        for action in actions:
            reason = _stop_reason() or (
                "consecutive_errors" if execution_context.stop_requested else None)
            if reason:
                stop_reason = stop_reason or reason
                break
            await _execute_action(action)

    if stop_reason:
        return {"task_id": task_id, "observations": observations, "assets": assets,
                "status": stop_reason}
    checkpoint = latest_checkpoint(db, task_id)
    completed_pages = int((checkpoint or {}).get("payload", {}).get("fofa_pages", 0))
    fofa_result = await executor.execute(
        fofa,
        {"query": "offline-fixture", "page": completed_pages + 1, "capability": "search"},
        execution_context,
    )
    pages_processed = completed_pages
    while True:
        with db.connect() as connection:
            for item in fofa_result.observations:
                _store_observation(connection, task_id, fofa_result, item)
                value = item.get("host") or item.get("ip") or item.get("target")
                if value:
                    normalized = normalize_target(str(value))
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO asset(asset_id,task_id,asset_type,normalized_value,scope_status,confidence) VALUES(?,?,?,?,?,?)",
                        (f"asset-{uuid.uuid4().hex[:16]}", task_id, normalized.target_type, normalized.normalized_value, "candidate", 0.5),
                    )
                    assets += cursor.rowcount
        pages_processed += 1
        create_checkpoint(
            db,
            task_id,
            phase="collecting_fofa",
            payload={
                "fofa_pages": pages_processed,
                "observations": observations,
                "assets": assets,
                "next_token": fofa_result.next_token,
            },
        )
        if not fofa_result.has_more:
            break
        reason = _stop_reason()
        if reason:
            return {"task_id": task_id, "observations": observations, "assets": assets,
                    "status": reason}
        fofa_result = await executor.execute(
            fofa,
            {"query": "offline-fixture", "page": pages_processed + 1, "next_token": fofa_result.next_token,
             "capability": "search"},
            execution_context,
        )
    with db.connect() as connection:
        connection.execute("INSERT INTO gap(gap_id,task_id,priority,reason) VALUES(?,?,?,?)",
                           (f"gap-{uuid.uuid4().hex[:16]}", task_id, "medium", "active_observation_not_authorized"))
        connection.execute("UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?", (TaskStatus.CORRELATING.value, task_id))
    create_checkpoint(
        db,
        task_id,
        phase="correlating",
        payload={"observations": observations, "assets": assets, "fofa_pages": pages_processed},
    )
    return {"task_id": task_id, "observations": observations, "assets": assets}


def _ensure_delegated_subtask(db: Database, task_id: str, adapter_name: str, scope_version: str) -> str:
    """Idempotent subtask row per adapter group; the lease table stays ready
    for real worker claims in the online phase."""
    idempotency_key = f"delegated:{task_id}:collecting_passive:{adapter_name}"
    existing = next(db.rows("SELECT subtask_id FROM subtask WHERE idempotency_key=?", (idempotency_key,)), None)
    if existing is not None:
        return existing["subtask_id"]
    subtask_id = f"subtask-{uuid.uuid4().hex[:16]}"
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO subtask(subtask_id,parent_task_id,role,phase,scope_version,
                                   allowed_tools_json,forbidden_actions_json,status,idempotency_key)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (subtask_id, task_id, f"worker:{adapter_name}", "collecting_passive", scope_version,
             json.dumps(["adapter:" + adapter_name]), json.dumps(["active_probing"]),
             "planned", idempotency_key),
        )
    return subtask_id


def _store_observation(connection, task_id: str, result, item: dict) -> None:
    raw = json.dumps(item, ensure_ascii=True, sort_keys=True)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    evidence_id = f"evidence-{uuid.uuid4().hex[:16]}"
    connection.execute("INSERT INTO evidence(evidence_id,task_id,source,summary,sha256,redaction_status) VALUES(?,?,?,?,?,?)",
                       (evidence_id, task_id, result.request_id or "adapter", raw[:500], digest, "not_needed"))
    connection.execute("INSERT INTO observation(observation_id,task_id,source,target,payload_json,evidence_id) VALUES(?,?,?,?,?,?)",
                       (f"observation-{uuid.uuid4().hex[:16]}", task_id, result.request_id or "adapter",
                        str(item.get("target") or item.get("host") or item.get("ip") or ""), raw, evidence_id))
