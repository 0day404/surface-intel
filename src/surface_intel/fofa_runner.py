from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters.base import TaskContext
from .adapters.fofa import FofaMcpAdapter
from .state.database import Database
from .state.fofa import FofaBatchRepository
from .planning import query_fingerprint
from .fofa_queries import split_fofa_query
from .redaction import redact
import hashlib
import json
import uuid


@dataclass(frozen=True)
class FofaRunResult:
    status: str
    observations: int
    pages: int
    cost_units: int
    batch_id: str
    consumed_fpoints: int | None = None
    required_fpoints: int | None = None
    error: str | None = None


def _store_fofa_observations(db: Database, task_id: str, result: Any) -> None:
    with db.connect() as connection:
        for item in result.observations:
            payload = redact(item)
            raw = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            evidence_id = f"evidence-{uuid.uuid4().hex[:16]}"
            connection.execute(
                """INSERT INTO evidence(evidence_id,task_id,source,summary,sha256,redaction_status)
                   VALUES(?,?,?,?,?,?)""",
                (evidence_id, task_id, "fofa_mcp", raw[:500],
                 hashlib.sha256(raw.encode()).hexdigest(), "redacted"),
            )
            connection.execute(
                """INSERT INTO observation(observation_id,task_id,source,target,payload_json,evidence_id)
                   VALUES(?,?,?,?,?,?)""",
                (f"observation-{uuid.uuid4().hex[:16]}", task_id, "fofa_mcp",
                 str(item.get("host") or item.get("ip") or item.get("target") or ""),
                 raw, evidence_id),
            )


async def run_fofa_query(
    db: Database,
    client: Any,
    *,
    task_id: str,
    scope_version: str,
    query: str,
    context: TaskContext,
    batch_index: int = 1,
    size: int = 100,
    max_pages: int = 20,
    parent_query_id: str | None = None,
    split_reason: str | None = None,
) -> FofaRunResult:
    if len(query) > 4096:
        raise ValueError("query_requires_split")
    fingerprint = query_fingerprint(query)
    batches = FofaBatchRepository(db)
    batch_id = batches.create_batch(
        task_id, fingerprint, query, scope_version,
        batch_index=batch_index, parent_query_id=parent_query_id,
        split_reason=split_reason,
    )
    existing = next(db.rows("SELECT status FROM fofa_query_batch WHERE batch_id=?", (batch_id,)), None)
    if existing and existing["status"] == "completed":
        row = next(db.rows("SELECT result_count,cost_units FROM fofa_query_batch WHERE batch_id=?", (batch_id,)), None)
        return FofaRunResult("already_completed", int(row["result_count"]), 0, int(row["cost_units"]), batch_id)
    if not batches.start(batch_id):
        return FofaRunResult("blocked", 0, 0, 0, batch_id, error="batch_not_planned")
    adapter = FofaMcpAdapter(client)
    token: str | None = None
    page = 1
    observations = 0
    cost = 0
    consumed_fpoints_total = 0
    required_fpoints_total = 0
    try:
        while page <= max_pages:
            action: dict[str, Any] = {
                "query": query,
                "size": size,
                "page": page,
                "confirm": True,
                "task_id": task_id,
                "request_id": f"surface-{task_id}-{fingerprint[:12]}-{page}",
            }
            if token:
                action["next_token"] = token
            result = await adapter.execute(action, context)
            if result.status != "completed":
                batches.fail(batch_id, result.error or result.status)
                return FofaRunResult(result.status, observations, page - 1, cost, batch_id, error=result.error)
            observations += len(result.observations)
            _store_fofa_observations(db, task_id, result)
            cost += result.cost_units
            consumed_fpoints_total += result.consumed_fpoints or 0
            required_fpoints_total += result.required_fpoints or 0
            token = result.next_token
            batches.complete(
                batch_id,
                request_id=result.request_id,
                next_token=token,
                cost_units=cost,
                result_count=observations,
                estimated_fpoints=required_fpoints_total,
                consumed_fpoints=consumed_fpoints_total,
                finished=not result.has_more,
            )
            if not result.has_more:
                return FofaRunResult(
                    "completed", observations, page, cost, batch_id,
                    consumed_fpoints_total, required_fpoints_total,
                )
            if not token:
                batches.fail(batch_id, "missing_next_token")
                return FofaRunResult("failed", observations, page, cost, batch_id, error="missing_next_token")
            batches.save_cursor(batch_id, task_id, fingerprint, token, page + 1, result.request_id)
            page += 1
        batches.fail(batch_id, "max_pages_exceeded")
        return FofaRunResult("partial", observations, page - 1, cost, batch_id, error="max_pages_exceeded")
    except Exception as exc:
        batches.fail(batch_id, type(exc).__name__)
        return FofaRunResult("failed", observations, page - 1, cost, batch_id, error=type(exc).__name__)


async def run_fofa_query_batches(
    db: Database,
    client: Any,
    *,
    task_id: str,
    scope_version: str,
    query: str,
    context: TaskContext,
    max_query_length: int = 4096,
    size: int = 100,
    max_pages: int = 20,
) -> list[FofaRunResult]:
    batches = split_fofa_query(query, max_query_length)
    parent_query_id = f"fofa-query-{query_fingerprint(query)[:24]}"
    FofaBatchRepository(db).create_plan(
        task_id, parent_query_id, query, query_fingerprint(query),
        purpose="domain_assets", target_ref=query,
        expected_fields=["ip", "port", "protocol", "host", "domain", "title", "product", "version"],
        source_basis=["user_authorized_scope"],
    )
    results: list[FofaRunResult] = []
    for batch in batches:
        results.append(await run_fofa_query(
            db, client, task_id=task_id, scope_version=scope_version,
            query=batch.query, context=context, batch_index=batch.batch_index,
            size=size, max_pages=max_pages, parent_query_id=parent_query_id,
            split_reason=batch.split_reason,
        ))
    return results


async def run_fofa_operation(
    db: Database,
    client: Any,
    *,
    task_id: str,
    scope_version: str,
    tool: str,
    arguments: dict[str, Any],
    context: TaskContext,
) -> FofaRunResult:
    """Run stats/host/account operations and persist their evidence without creating assets."""
    adapter = FofaMcpAdapter(client)
    payload = dict(arguments)
    payload["tool"] = tool
    result = await adapter.execute(payload, context)
    _store_fofa_observations(db, task_id, result)
    return FofaRunResult(
        result.status,
        len(result.observations),
        1,
        result.cost_units,
        f"fofa-operation-{uuid.uuid4().hex[:16]}",
        consumed_fpoints=result.consumed_fpoints,
        required_fpoints=result.required_fpoints,
        error=result.error,
    )
