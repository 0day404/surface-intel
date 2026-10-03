from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from ..states import ActionStatus
from .base import AdapterManifest, AdapterResult, TaskContext

_DEFINITIVE_STATUSES = {"observed", "empty"}
_INFRA_STATUSES = {"rate_limited", "timeout", "http_error"}


class CtLogClient(Protocol):
    async def search(self, domain: str) -> dict[str, Any]: ...


class InjectedCtLogClient:
    """Transport-neutral client for tests or host-provided CT log sessions."""

    def __init__(self, search: Callable[[str], Awaitable[dict[str, Any]]]):
        self._search = search

    async def search(self, domain: str) -> dict[str, Any]:
        return await self._search(domain)


class CertificateTransparencyAdapter:
    """Thin passive certificate-transparency adapter; the log client is injected.

    The adapter only ever queries names handed to it by the runner; it cannot
    enumerate log identities on its own. Certificates come back as structured
    rows; whether their SANs imply new in-scope relations is decided downstream
    (SANs are candidate relations only, never ownership confirmation).
    """

    manifest = AdapterManifest(
        "ct_log", "1.0", ("ct_search",),
        required_active_level="passive_only",
        cost_units=1,
        retry_policy="idempotent",
    )

    def __init__(self, client: CtLogClient | Callable[[str], Awaitable[dict[str, Any]]]):
        self.client = client if hasattr(client, "search") else InjectedCtLogClient(client)

    async def health(self, context: TaskContext) -> AdapterResult:
        health = getattr(self.client, "health", None)
        if health is None:
            return AdapterResult("unknown", error="not_configured:ct_client_health_unavailable")
        try:
            payload = await health()
        except (LookupError, RuntimeError) as exc:
            return AdapterResult("not_configured", error=f"not_configured:{type(exc).__name__}")
        status = ActionStatus.COMPLETED.value if payload.get("ok") else ActionStatus.FAILED.value
        return AdapterResult(status, observations=[payload], error=payload.get("error"))

    async def validate(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        if not str(action.get("target", "")).strip():
            return AdapterResult(ActionStatus.BLOCKED.value, error="target_required")
        return AdapterResult(ActionStatus.COMPLETED.value)

    async def execute(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        target = str(action.get("target", "")).strip().rstrip(".").lower()
        try:
            outcome = await self.client.search(target)
        except TimeoutError:
            return AdapterResult(
                ActionStatus.TIMED_OUT.value,
                observations=self._empty_observation(target, "timeout"),
                error="timeout",
            )
        except Exception as exc:  # transport death; classification stays coarse on purpose
            return AdapterResult(
                ActionStatus.FAILED.value,
                observations=self._empty_observation(target, "transport_error"),
                error=f"ct:{type(exc).__name__}",
            )
        status = str(outcome.get("status", "")).lower()
        if status in _INFRA_STATUSES:
            terminal = ActionStatus.TIMED_OUT.value if status == "timeout" else ActionStatus.FAILED.value
            return AdapterResult(
                terminal,
                observations=self._empty_observation(target, status, outcome=outcome),
                error=status,
            )
        if status not in _DEFINITIVE_STATUSES:
            return AdapterResult(
                ActionStatus.FAILED.value,
                observations=self._empty_observation(target, status or "unknown_status", outcome=outcome),
                error=f"invalid_client_status:{status}",
            )
        certificates = outcome.get("certificates")
        if not isinstance(certificates, list):
            certificates = []
        observation = {
            "target": target,
            "certificates": [self._certificate_row(item) for item in certificates],
            "matched_count": len(certificates),
            "status": status,
            "log_source": outcome.get("log_source"),
            "queried_at": outcome.get("queried_at"),
            "query_ms": outcome.get("query_ms"),
            # present only when a failover client switched sources upstream
            "failover": outcome.get("failover"),
            "source": self.manifest.name,
        }
        return AdapterResult(
            ActionStatus.COMPLETED.value,
            observations=[observation],
            cost_units=1,
            request_id=f"ct-{context.task_id}-{target}",
        )

    async def cancel(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        return AdapterResult(ActionStatus.CANCELLED.value, error="ct_cancellation_not_supported")

    @staticmethod
    def _certificate_row(item: Any) -> dict[str, Any]:
        row = item if isinstance(item, dict) else {}
        sans = row.get("sans")
        if not isinstance(sans, list):
            sans = []
        return {
            "common_name": row.get("common_name"),
            "sans": [str(value) for value in sans],
            "issuer": row.get("issuer"),
            "serial": row.get("serial"),
            "not_before": row.get("not_before"),
            "not_after": row.get("not_after"),
            "first_seen": row.get("first_seen"),
            "last_seen": row.get("last_seen"),
            "log_id": row.get("log_id"),
            "log_source": row.get("log_source") or "crt.sh",
            # CT-source completeness markers (set by sources whose search rows
            # do not carry the full SAN list); None for crt.sh rows.
            "san_count": row.get("san_count"),
            "san_completeness": row.get("san_completeness"),
        }

    @staticmethod
    def _empty_observation(
        target: str,
        status: str,
        outcome: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return [{
            "target": target,
            "certificates": [],
            "matched_count": 0,
            "status": status,
            "log_source": (outcome or {}).get("log_source"),
            "queried_at": (outcome or {}).get("queried_at"),
            "query_ms": (outcome or {}).get("query_ms"),
            "failover": (outcome or {}).get("failover"),
            "source": CertificateTransparencyAdapter.manifest.name,
        }]
