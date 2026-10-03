from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .adapters.base import Adapter, AdapterManifest, AdapterResult, TaskContext
from .adapters.capabilities import CapabilityRegistry
from .redaction import redact
from .policy.scope import Scope
from .state.budget import BudgetRepository
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
    stop_reason: str | None = None
    rate_limiter: Any = None  # RateLimiter; Any to avoid an import cycle
    budget_ledger: Any = None  # BudgetLedger; Any to avoid an import cycle
    action_timeout: float | None = None
    max_consecutive_errors: int | None = None
    consecutive_errors: int = 0
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

    def scope(self) -> Scope | None:
        payload = self.metadata.get("scope_payload")
        return Scope.from_dict(payload) if isinstance(payload, dict) else None


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
        self.budgets = BudgetRepository(db)

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
                return self._finish(call_id, validation, AdapterResult(ActionStatus.BLOCKED.value, error="stop_requested"), context)
            if not context.scope_confirmed:
                return self._finish(call_id, validation, AdapterResult(ActionStatus.BLOCKED.value, error="scope_not_confirmed"), context)
            scope = context.scope()
            target = str(action.get("target", "")).strip()
            if scope is not None:
                if context.scope_version != scope.scope_version:
                    return self._finish(call_id, validation, AdapterResult(ActionStatus.BLOCKED.value, error="scope_version_mismatch"), context)
                if target:
                    allowed, reason = scope.is_action_allowed(
                        target=target,
                        active=manifest.required_active_level != "passive_only",
                    )
                    if not allowed:
                        return self._finish(call_id, validation, AdapterResult(ActionStatus.BLOCKED.value, error=reason), context)
            if manifest.capability_status != CapabilityStatus.AVAILABLE.value:
                return self._finish(
                    call_id,
                    validation,
                    AdapterResult(ActionStatus.BLOCKED.value, error=f"adapter_{manifest.capability_status}"),
                    context,
                )
            capability = action.get("capability") or (manifest.capabilities[0] if manifest.capabilities else "")
            check = self.registry.check(
                manifest.name,
                capability=capability,
                active_level=context.active_level,
                scope_confirmed=context.scope_confirmed,
            )
            if not check.ok:
                return self._finish(call_id, validation, AdapterResult(ActionStatus.BLOCKED.value, error=";".join(check.reasons)), context)
            validation = await adapter.validate(action, context.as_task_context())
            if validation.status != ActionStatus.COMPLETED.value:
                return self._finish(call_id, validation, validation, context)
            budget_kind = str(action.get("budget_kind", manifest.name))
            ledger_kinds: list[str] | None = None
            if context.budget_ledger is not None:
                # multi-level ledger is authoritative when present: task,
                # phase, target and adapter budgets reserve atomically
                ledger_kinds = context.budget_ledger.reserve(
                    phase=action.get("phase"), target=target or None,
                    adapter=manifest.name, units=manifest.cost_units)
                if ledger_kinds is None:
                    return self._finish(call_id, validation, AdapterResult(ActionStatus.BLOCKED.value, error="budget_exhausted"), context)
            db_reserved = next(self.db.rows(
                "SELECT 1 FROM budget WHERE task_id=? AND kind=?",
                (context.task_id, budget_kind),
            ), None) is not None
            reserved = self.budgets.reserve(context.task_id, budget_kind, manifest.cost_units) if db_reserved else context.reserve_cost(manifest.cost_units)
            if not reserved:
                return self._finish(call_id, validation, AdapterResult(ActionStatus.BLOCKED.value, error="budget_exhausted"), context)
            if context.rate_limiter is not None:
                keys = [f"adapter:{manifest.name}"]
                if target:
                    keys.append(f"target:{target}")
                await context.rate_limiter.acquire(*keys)
            try:
                if context.action_timeout is not None:
                    result = await asyncio.wait_for(
                        adapter.execute(action, context.as_task_context()), context.action_timeout)
                else:
                    result = await adapter.execute(action, context.as_task_context())
            except (asyncio.TimeoutError, TimeoutError):
                result = await self._timed_out_result(adapter, action, context)
            if ledger_kinds is not None:
                # committed even on failed/timed_out results: spent facts stay
                context.budget_ledger.commit(ledger_kinds, manifest.cost_units)
            if db_reserved:
                self.budgets.commit(context.task_id, budget_kind, manifest.cost_units)
            if result.cost_units > manifest.cost_units and not context.reserve_cost(result.cost_units - manifest.cost_units):
                result = AdapterResult(ActionStatus.PARTIAL.value, observations=result.observations, error="budget_exhausted",
                                       request_id=result.request_id)
            return self._finish(call_id, validation, result, context)
        except (asyncio.TimeoutError, TimeoutError):
            result = await self._timed_out_result(adapter, action, context)
            return self._finish(call_id, validation, result, context)
        except Exception as exc:
            return self._finish(call_id, validation, AdapterResult(ActionStatus.FAILED.value, error=f"remote_error:{type(exc).__name__}"), context)

    def _insert_call(self, call_id: str, context: ExecutionContext, action_id: str | None,
                     manifest: AdapterManifest, input_summary: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO adapter_call
                   (call_id,task_id,action_id,adapter_name,adapter_version,status,input_summary)
                   VALUES(?,?,?,?,?,?,?)""",
                (call_id, context.task_id, action_id, manifest.name, manifest.version, "started", input_summary),
            )

    async def _timed_out_result(self, adapter: Adapter, action: dict[str, Any], context: ExecutionContext) -> AdapterResult:
        """Timeout: try to cancel; a failed or unsupported cancel leaves the
        external end-state unknown (plan R5 - never pretend we know)."""
        result = AdapterResult(ActionStatus.TIMED_OUT.value, error="timeout", external_state="unknown")
        try:
            cancellation = await adapter.cancel(action, context.as_task_context())
            if cancellation.status == ActionStatus.CANCELLED.value:
                result.external_state = "cancelled"
        except Exception:
            result.external_state = "unknown"
        return result

    def _finish(self, call_id: str, validation: AdapterResult | None, result: AdapterResult,
                context: ExecutionContext | None = None) -> AdapterResult:
        error_code = classify_error(result.error)
        with self.db.connect() as connection:
            connection.execute(
                """UPDATE adapter_call SET status=?, request_id=?, error_code=?, completed_at=?
                   WHERE call_id=?""",
                (result.status, result.request_id, error_code, datetime.now(timezone.utc).isoformat(), call_id),
            )
        if context is not None:
            if result.status in {ActionStatus.FAILED.value, ActionStatus.TIMED_OUT.value}:
                context.consecutive_errors += 1
                limit = context.max_consecutive_errors
                if limit is not None and context.consecutive_errors >= limit:
                    context.stop_requested = True
                    context.stop_reason = "consecutive_errors"
            elif result.status == ActionStatus.COMPLETED.value:
                context.consecutive_errors = 0
        return result
