from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters.base import TaskContext
from .adapters.fofa import FofaMcpAdapter
from .state.database import Database
from .state.fofa import FofaBatchRepository
from .planning import query_fingerprint


@dataclass(frozen=True)
class FofaRunResult:
    status: str
    observations: int
    pages: int
    cost_units: int
    batch_id: str
    error: str | None = None


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
) -> FofaRunResult:
    fingerprint = query_fingerprint(query)
    batches = FofaBatchRepository(db)
    batch_id = batches.create_batch(task_id, fingerprint, query, scope_version, batch_index=batch_index)
    existing = next(db.rows("SELECT status FROM fofa_query_batch WHERE batch_id=?", (batch_id,)), None)
    if existing and existing["status"] == "completed":
        row = next(db.rows("SELECT result_count,cost_units FROM fofa_query_batch WHERE batch_id=?", (batch_id,)), None)
        return FofaRunResult("already_completed", int(row["result_count"]), 0, int(row["cost_units"]), batch_id)
    if not batches.start(batch_id):
        return FofaRunResult("blocked", 0, 0, 0, batch_id, "batch_not_planned")
    adapter = FofaMcpAdapter(client)
    token: str | None = None
    page = 1
    observations = 0
    cost = 0
    try:
        while page <= max_pages:
            action: dict[str, Any] = {
                "query": query,
                "size": size,
                "page": page,
                "request_id": f"surface-{task_id}-{fingerprint[:12]}-{page}",
            }
            if token:
                action["next_token"] = token
            result = await adapter.execute(action, context)
            if result.status != "completed":
                batches.fail(batch_id, result.error or result.status)
                return FofaRunResult(result.status, observations, page - 1, cost, batch_id, result.error)
            observations += len(result.observations)
            cost += result.cost_units
            token = result.next_token
            batches.complete(
                batch_id,
                request_id=result.request_id,
                next_token=token,
                cost_units=cost,
                result_count=observations,
                finished=not result.has_more,
            )
            if not result.has_more:
                return FofaRunResult("completed", observations, page, cost, batch_id)
            if not token:
                batches.fail(batch_id, "missing_next_token")
                return FofaRunResult("failed", observations, page, cost, batch_id, "missing_next_token")
            batches.save_cursor(batch_id, task_id, fingerprint, token, page + 1, result.request_id)
            page += 1
        batches.fail(batch_id, "max_pages_exceeded")
        return FofaRunResult("partial", observations, page - 1, cost, batch_id, "max_pages_exceeded")
    except Exception as exc:
        batches.fail(batch_id, type(exc).__name__)
        return FofaRunResult("failed", observations, page - 1, cost, batch_id, type(exc).__name__)
