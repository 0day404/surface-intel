from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from ..states import ActionStatus
from .base import AdapterManifest, AdapterResult, TaskContext

DNS_RECORD_TYPES = ("A", "AAAA", "CNAME", "NS", "MX", "TXT", "SOA")
_DEFINITIVE_STATUSES = {"observed", "empty", "nxdomain"}
_INFRA_STATUSES = {"servfail", "refused", "rate_limited", "timeout"}


class DnsResolverClient(Protocol):
    async def resolve(self, name: str, record_type: str) -> dict[str, Any]: ...


class InjectedDnsResolverClient:
    """Transport-neutral client for tests or host-provided resolver sessions."""

    def __init__(self, resolve: Callable[[str, str], Awaitable[dict[str, Any]]]):
        self._resolve = resolve

    async def resolve(self, name: str, record_type: str) -> dict[str, Any]:
        return await self._resolve(name, record_type)


class DnsAdapter:
    """Thin passive DNS adapter; the resolver client is injected.

    Record types are whitelist-only (A/AAAA/CNAME/NS/MX/TXT/SOA) so zone
    transfers or arbitrary meta-queries can never be expressed through it.
    """

    manifest = AdapterManifest(
        "dns_resolver", "1.0", ("dns_resolve",),
        required_active_level="passive_only",
        cost_units=1,
        retry_policy="idempotent",
    )

    def __init__(self, client: DnsResolverClient | Callable[[str, str], Awaitable[dict[str, Any]]]):
        self.client = client if hasattr(client, "resolve") else InjectedDnsResolverClient(client)

    async def health(self, context: TaskContext) -> AdapterResult:
        health = getattr(self.client, "health", None)
        if health is None:
            return AdapterResult("unknown", error="not_configured:dns_client_health_unavailable")
        try:
            payload = await health()
        except (LookupError, RuntimeError) as exc:
            return AdapterResult("not_configured", error=f"not_configured:{type(exc).__name__}")
        status = ActionStatus.COMPLETED.value if payload.get("ok") else ActionStatus.FAILED.value
        return AdapterResult(status, observations=[payload], error=payload.get("error"))

    async def validate(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        if not str(action.get("target", "")).strip():
            return AdapterResult(ActionStatus.BLOCKED.value, error="target_required")
        record_types = action.get("record_types") or list(DNS_RECORD_TYPES)
        if not isinstance(record_types, list) or not record_types:
            return AdapterResult(ActionStatus.BLOCKED.value, error="record_types_required")
        invalid = [item for item in record_types if str(item).upper() not in DNS_RECORD_TYPES]
        if invalid:
            return AdapterResult(ActionStatus.BLOCKED.value, error="invalid_record_type")
        return AdapterResult(ActionStatus.COMPLETED.value)

    async def execute(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        target = str(action.get("target", "")).strip().rstrip(".").lower()
        record_types = [str(item).upper() for item in (action.get("record_types") or DNS_RECORD_TYPES)]
        observations: list[dict[str, Any]] = []
        for record_type in record_types:
            try:
                outcome = await self.client.resolve(target, record_type)
            except TimeoutError:
                return AdapterResult(
                    ActionStatus.TIMED_OUT.value, observations=self._infra_observation(target, record_type, "timeout", observations),
                    error="timeout",
                )
            except Exception as exc:  # transport death; classification stays coarse on purpose
                return AdapterResult(
                    ActionStatus.FAILED.value, observations=observations,
                    error=f"dns:{type(exc).__name__}",
                )
            status = str(outcome.get("status", "observed")).lower()
            if status in _INFRA_STATUSES:
                terminal = ActionStatus.TIMED_OUT.value if status == "timeout" else ActionStatus.FAILED.value
                return AdapterResult(
                    terminal,
                    observations=self._infra_observation(target, record_type, status, observations, outcome),
                    error=status,
                )
            if status not in _DEFINITIVE_STATUSES:
                return AdapterResult(
                    ActionStatus.FAILED.value, observations=observations,
                    error=f"invalid_resolver_status:{status}",
                )
            observations.append({
                "target": target,
                "record_type": record_type,
                "records": [str(value) for value in outcome.get("records", ())],
                "ttl": outcome.get("ttl"),
                "resolver": outcome.get("resolver"),
                "resolved_at": outcome.get("resolved_at"),
                "resolution_ms": outcome.get("resolution_ms"),
                "dnssec": outcome.get("dnssec"),
                "status": status,
                "source": self.manifest.name,
            })
        return AdapterResult(
            ActionStatus.COMPLETED.value,
            observations=observations,
            cost_units=len(record_types),
            request_id=f"dns-{context.task_id}-{target}",
        )

    async def cancel(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        return AdapterResult(ActionStatus.CANCELLED.value, error="dns_cancellation_not_supported")

    @staticmethod
    def _infra_observation(
        target: str,
        record_type: str,
        status: str,
        collected: list[dict[str, Any]],
        outcome: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "target": target,
            "record_type": record_type,
            "records": [],
            "ttl": (outcome or {}).get("ttl"),
            "resolver": (outcome or {}).get("resolver"),
            "resolved_at": (outcome or {}).get("resolved_at"),
            "resolution_ms": (outcome or {}).get("resolution_ms"),
            "dnssec": (outcome or {}).get("dnssec"),
            "status": status,
            "source": DnsAdapter.manifest.name,
        }
        return [*collected, payload]
