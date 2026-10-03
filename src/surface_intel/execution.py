from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .adapters.base import Adapter, AdapterManifest, AdapterResult, TaskContext
from .adapters.capabilities import CapabilityRegistry
from .redaction import redact
from .state.database import Database
from .states import ActionStatus, CapabilityStatus


@dataclass
class ExecutionContext:
    task_id: str
    scope_version: str
    scope_confirmed: bool
    active_level: str = "passive_only"
    budget: dict[str, int] = field(default_factory=dict)
    evidence_root: str = ""
    correlation_id: str = field(default_factory=lambda: f"corr-{uuid.uuid4().hex[:16]}")
    stop_requested: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_task_context(self) -> TaskContext:
        metadata = dict(self.metadata)
        metadata.update({
            "scope_confirmed": self.scope_confirmed,
            "active_level": self.active_level,
            "correlation_id": self.correlation_id,
        })
        return TaskContext(self.task_id, self.scope_version, metadata)

    def reserve_cost(self, units: int) -> bool:
        remaining = self.budget.get("cost_units")
        if remaining is None:
            return True
        if units < 0 or units > remaining:
            return False
        self.budget["cost_units"] = remaining - units
        return True


def classify_error(error: str | None) -> str | None:
    if not error:
        return None
    value = error.lower()
    if value in {"scope_not_confirmed", "stop_requested"} or value.startswith("adapter_"):
        return "policy_blocked"
    if value == "budget_exhausted":
        return "budget_exhausted"
    for marker, category in (
        ("timeout", "timeout"),
        ("rate", "rate_limited"),
        ("auth", "auth_unavailable"),
        ("credential", "auth_unavailable"),
        ("not_configured", "dependency_unavailable"),
        ("transport", "dependency_unavailable"),
        ("invalid", "invalid_input"),
        ("unknown", "unknown_external_state"),
    ):
        if marker in value:
            return category
    return "remote_error"


class AdapterExecutor:
    def __init__(self, db: Database, registry: CapabilityRegistry):
        self.db = db
        self.registry = registry

    async def execute(
        self,
        adapter: Adapter,
        action: dict[str, Any],
        context: ExecutionContext,
    ) -> AdapterResult:
        manifest: AdapterManifest = adapter.manifest
        action_id = action.get("action_id")
        call_id = f"call-{uuid.uuid4().hex[:16]}"
        input_summary = json.dumps(redact(action), ensure_ascii=True, sort_keys=True)[:2000]
        self._insert_call(call_id, context, action_id, manifest, input_summary)
        validation: AdapterResult | None = None
        try:
            if context.stop_requested:
                return self._finish(call_id, validation, AdapterResult(ActionStatus.BLOCKED.value, error="stop_requested"))
            if not context.scope_confirmed:
                return self._finish(call_id, validation, AdapterResult(ActionStatus.BLOCKED.value, error="scope_not_confirmed"))
            if manifest.capability_status != CapabilityStatus.AVAILABLE.value:
                return self._finish(
                    call_id,
                    validation,
                    AdapterResult(ActionStatus.BLOCKED.value, error=f"adapter_{manifest.capability_status}"),
                )
            capability = action.get("capability") or (manifest.capabilities[0] if manifest.capabilities else "")
            check = self.registry.check(
                manifest.name,
                capability=capability,
                active_level=context.active_level,
                scope_confirmed=context.scope_confirmed,
            )
            if not check.ok:
                return self._finish(call_id, validation, AdapterResult(ActionStatus.BLOCKED.value, error=";".join(check.reasons)))
            validation = await adapter.validate(action, context.as_task_context())
            if validation.status != ActionStatus.COMPLETED.value:
                return self._finish(call_id, validation, validation)
            if not context.reserve_cost(manifest.cost_units):
                return self._finish(call_id, validation, AdapterResult(ActionStatus.BLOCKED.value, error="budget_exhausted"))
            result = await adapter.execute(action, context.as_task_context())
            if result.cost_units > manifest.cost_units and not context.reserve_cost(result.cost_units - manifest.cost_units):
                result = AdapterResult(ActionStatus.PARTIAL.value, observations=result.observations, error="budget_exhausted",
                                       request_id=result.request_id)
            return self._finish(call_id, validation, result)
        except TimeoutError:
            return self._finish(call_id, validation, AdapterResult(ActionStatus.TIMED_OUT.value, error="timeout"))
        except Exception as exc:
            return self._finish(call_id, validation, AdapterResult(ActionStatus.FAILED.value, error=f"remote_error:{type(exc).__name__}"))

    def _insert_call(self, call_id: str, context: ExecutionContext, action_id: str | None,
                     manifest: AdapterManifest, input_summary: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO adapter_call
                   (call_id,task_id,action_id,adapter_name,adapter_version,status,input_summary)
                   VALUES(?,?,?,?,?,?,?)""",
                (call_id, context.task_id, action_id, manifest.name, manifest.version, "started", input_summary),
            )

    def _finish(self, call_id: str, validation: AdapterResult | None, result: AdapterResult) -> AdapterResult:
        error_code = classify_error(result.error)
        with self.db.connect() as connection:
            connection.execute(
                """UPDATE adapter_call SET status=?, request_id=?, error_code=?, completed_at=?
                   WHERE call_id=?""",
                (result.status, result.request_id, error_code, datetime.now(timezone.utc).isoformat(), call_id),
            )
        return result
