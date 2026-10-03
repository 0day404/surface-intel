from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from .adapters.base import TaskContext
from .adapters.mock import MockFofaAdapter, MockPassiveAdapter
from .normalization import normalize_target
from .state.database import Database
from .states import ActionStatus, TaskStatus


async def run_offline_task(db: Database, task_id: str, fofa_pages: list[dict] | None = None) -> dict[str, int | str]:
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (task_id,)), None)
    if task is None:
        raise ValueError("task_not_found")
    scope_row = next(db.rows("SELECT * FROM scope_snapshot WHERE task_id=? ORDER BY created_at DESC", (task_id,)), None)
    if scope_row is None or not scope_row["confirmed"]:
        raise ValueError("scope_not_confirmed")
    context = TaskContext(task_id, scope_row["scope_version"])
    passive = MockPassiveAdapter()
    fofa = MockFofaAdapter(fofa_pages or [{"observations": [], "has_more": False}])
    observations = 0
    assets = 0
    with db.connect() as connection:
        connection.execute("UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?", (TaskStatus.COLLECTING_PASSIVE.value, task_id))
    actions = list(db.rows("SELECT * FROM action WHERE task_id=? AND status=?", (task_id, ActionStatus.PLANNED.value)))
    for action in actions:
        result = await passive.execute({"target": action["idempotency_key"]}, context)
        with db.connect() as connection:
            for item in result.observations:
                _store_observation(connection, task_id, result, item)
                observations += 1
            connection.execute("UPDATE action SET status=?, result_ref=?, updated_at=CURRENT_TIMESTAMP WHERE action_id=?",
                               (result.status, result.request_id, action["action_id"]))
    fofa_result = await fofa.execute({"query": "offline-fixture", "page": 1}, context)
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
        if not fofa_result.has_more:
            break
        fofa_result = await fofa.execute({"query": "offline-fixture", "page": len(fofa.calls) + 1, "next_token": fofa_result.next_token}, context)
    with db.connect() as connection:
        connection.execute("INSERT INTO gap(gap_id,task_id,priority,reason) VALUES(?,?,?,?)",
                           (f"gap-{uuid.uuid4().hex[:16]}", task_id, "medium", "active_observation_not_authorized"))
        connection.execute("UPDATE task SET status=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?", (TaskStatus.CORRELATING.value, task_id))
    return {"task_id": task_id, "observations": observations, "assets": assets}


def _store_observation(connection, task_id: str, result, item: dict) -> None:
    raw = json.dumps(item, ensure_ascii=True, sort_keys=True)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    evidence_id = f"evidence-{uuid.uuid4().hex[:16]}"
    connection.execute("INSERT INTO evidence(evidence_id,task_id,source,summary,sha256,redaction_status) VALUES(?,?,?,?,?,?)",
                       (evidence_id, task_id, result.request_id or "adapter", raw[:500], digest, "not_needed"))
    connection.execute("INSERT INTO observation(observation_id,task_id,source,target,payload_json,evidence_id) VALUES(?,?,?,?,?,?)",
                       (f"observation-{uuid.uuid4().hex[:16]}", task_id, result.request_id or "adapter",
                        str(item.get("target") or item.get("host") or item.get("ip") or ""), raw, evidence_id))
