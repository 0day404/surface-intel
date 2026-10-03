from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from ..states import ActionStatus
from .base import AdapterManifest, AdapterResult, TaskContext

TARGET_KINDS = ("domain", "ip", "asn")
_DEFINITIVE_STATUSES = {"observed", "not_found"}
_INFRA_STATUSES = {"rate_limited", "timeout", "http_error", "unsupported", "invalid_response"}


class RdapLookupClient(Protocol):
    async def lookup(self, target: str, kind: str) -> dict[str, Any]: ...


class InjectedRdapClient:
    """Transport-neutral client for tests or host-provided RDAP sessions."""

    def __init__(self, lookup: Callable[[str, str], Awaitable[dict[str, Any]]]):
        self._lookup = lookup

    async def lookup(self, target: str, kind: str) -> dict[str, Any]:
        return await self._lookup(target, kind)


class RdapAdapter:
    """Thin passive registration-data adapter; the RDAP/WHOIS client is injected.

    The adapter only looks up names, IPs or ASNs handed to it by the runner.
    Registration records describe who runs an authorized target; they never
    confirm attribution by themselves — organization or ASN equality is only
    ever a candidate relation decided downstream.
    """

    manifest = AdapterManifest(
        "rdap_lookup", "1.0", ("rdap_lookup",),
        required_active_level="passive_only",
        cost_units=1,
        retry_policy="idempotent",
    )

    def __init__(self, client: RdapLookupClient | Callable[[str, str], Awaitable[dict[str, Any]]]):
        self.client = client if hasattr(client, "lookup") else InjectedRdapClient(client)

    async def health(self, context: TaskContext) -> AdapterResult:
        health = getattr(self.client, "health", None)
        if health is None:
            return AdapterResult("unknown", error="not_configured:rdap_client_health_unavailable")
        try:
            payload = await health()
        except (LookupError, RuntimeError) as exc:
            return AdapterResult("not_configured", error=f"not_configured:{type(exc).__name__}")
        status = ActionStatus.COMPLETED.value if payload.get("ok") else ActionStatus.FAILED.value
        return AdapterResult(status, observations=[payload], error=payload.get("error"))

    async def validate(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        if not str(action.get("target", "")).strip():
            return AdapterResult(ActionStatus.BLOCKED.value, error="target_required")
        kind = str(action.get("kind", "")).strip().lower()
        if kind not in TARGET_KINDS:
            return AdapterResult(ActionStatus.BLOCKED.value, error="invalid_target_kind")
        return AdapterResult(ActionStatus.COMPLETED.value)

    async def execute(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        target = str(action.get("target", "")).strip().rstrip(".").lower()
        kind = str(action.get("kind", "")).strip().lower()
        try:
            outcome = await self.client.lookup(target, kind)
        except TimeoutError:
            return AdapterResult(
                ActionStatus.TIMED_OUT.value,
                observations=self._empty_observation(target, kind, "timeout"),
                error="timeout",
            )
        except Exception as exc:  # transport death; classification stays coarse on purpose
            return AdapterResult(
                ActionStatus.FAILED.value,
                observations=self._empty_observation(target, kind, "transport_error"),
                error=f"rdap:{type(exc).__name__}",
            )
        status = str(outcome.get("status", "")).lower()
        if status in _INFRA_STATUSES:
            terminal = ActionStatus.TIMED_OUT.value if status == "timeout" else ActionStatus.FAILED.value
            return AdapterResult(
                terminal,
                observations=self._empty_observation(target, kind, status),
                error=status,
            )
        if status not in _DEFINITIVE_STATUSES:
            return AdapterResult(
                ActionStatus.FAILED.value,
                observations=self._empty_observation(target, kind, status or "unknown_status"),
                error=f"invalid_client_status:{status}",
            )
        record = outcome.get("record") if isinstance(outcome.get("record"), dict) else {}
        observation = {
            "target": target,
            "target_kind": kind,
            "record": record,
            "status": status,
            "source_protocol": outcome.get("source_protocol"),
            "queried_at": outcome.get("queried_at"),
            "query_ms": outcome.get("query_ms"),
            "source": self.manifest.name,
        }
        return AdapterResult(
            ActionStatus.COMPLETED.value,
            observations=[observation],
            cost_units=1,
            request_id=f"rdap-{context.task_id}-{kind}-{target}",
        )

    async def cancel(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        return AdapterResult(ActionStatus.CANCELLED.value, error="rdap_cancellation_not_supported")

    @staticmethod
    def _empty_observation(target: str, kind: str, status: str) -> list[dict[str, Any]]:
        return [{
            "target": target,
            "target_kind": kind,
            "record": {},
            "status": status,
            "source_protocol": None,
            "queried_at": None,
            "query_ms": None,
            "source": RdapAdapter.manifest.name,
        }]
