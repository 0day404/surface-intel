from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .adapters.base import AdapterResult
from .adapters.capabilities import CapabilityRegistry
from .adapters.ct import CertificateTransparencyAdapter
from .ct_client import CrtShClient, FailoverCtClient
from .execution import AdapterExecutor, ExecutionContext
from .pacing import configured_rate_limiter
from .policy.scope import Scope
from .state.database import Database
from .state.observations import store_unified_observations


@dataclass(frozen=True)
class CtRunResult:
    status: str
    observations: int
    certificates: int
    error: str | None = None
    # D1: failover markers make primary degradation visible in run summaries
    failovers: int = 0


def classify_certificates_scope(scope: Scope, certificates: list[dict[str, Any]]) -> str:
    """Certificate matches never confirm ownership; SANs are candidate relations.

    A certificate matching an authorized domain does not authorize the other
    names it carries. When every name on the certificate falls inside the
    authorized scope the relation is still only `candidate`; any third-party or
    out-of-scope name escalates the observation to `needs_review`.
    """
    names: list[str] = []
    for certificate in certificates:
        common_name = certificate.get("common_name")
        if common_name:
            names.append(str(common_name))
        names.extend(str(item) for item in certificate.get("sans", ()))
    if not names:
        return "not_applicable"
    for name in names:
        if not _name_in_scope(scope, name):
            return "needs_review"
    return "candidate"


def _name_in_scope(scope: Scope, name: str) -> bool:
    candidate = name.strip().rstrip(".").lower()
    if candidate.startswith("*."):
        candidate = candidate[2:]
    return bool(candidate) and scope.is_in_scope(candidate)


def ensure_failover(client, fallback=None):
    """Retrospective P0-1: a bare CrtShClient silently loses the ctlogs.dev
    failover real operations depend on. Wrap it (idempotent); callers who
    pinned a single source pass a CrtlogsDevClient/other client explicitly."""
    from .ct_client import CrtlogsDevClient, FailoverCtClient

    if isinstance(client, (FailoverCtClient, CrtlogsDevClient)):
        return client
    if isinstance(client, CrtShClient):
        fallback = fallback or CrtlogsDevClient(timeout=client.timeout)
        return FailoverCtClient(client, fallback)
    return client


def _action_id(domain: str) -> str:
    digest = hashlib.sha256(domain.encode()).hexdigest()[:12]
    return f"ct-{digest}"


def _store_ct_observations(
    db: Database,
    task_id: str,
    scope: Scope,
    result: AdapterResult,
) -> int:
    def metadata_for(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_quality": "direct",
            "license_status": "public_ct_log",
            "related_scope_status": classify_certificates_scope(
                scope, list(payload.get("certificates", []))
            ),
        }

    return store_unified_observations(
        db, task_id, scope, result, source="ct_log", metadata_for=metadata_for,
    )


async def run_ct_search(
    db: Database,
    client: Any,
    *,
    task_id: str,
    scope: Scope,
    domain: str,
    failover_client: Any | None = None,
) -> CtRunResult:
    """Passive CT log search for one authorized domain, one gated action.

    Gates before any client call: confirmed scope and target in scope. The
    search itself is a single action through the unified executor, so it gets
    `adapter_call` auditing and a transactional `ct_log` budget reservation.
    Certificates are persisted as observations with per-run scope
    classification; SANs never create assets and never confirm ownership.
    """
    normalized_domain = domain.strip().rstrip(".").lower()
    if not scope.confirmed:
        return CtRunResult("blocked", 0, 0, error="scope_not_confirmed")
    if not scope.is_in_scope(normalized_domain):
        return CtRunResult("blocked", 0, 0, error="target_out_of_scope")

    # retrospective P0-1: ensure the ctlogs.dev failover survives a bare
    # CrtShClient even when the caller forgot configured_ct_client()
    client = ensure_failover(client, failover_client)
    adapter = CertificateTransparencyAdapter(client)
    registry = CapabilityRegistry([adapter.manifest])
    executor = AdapterExecutor(db, registry)
    context = ExecutionContext(
        rate_limiter=configured_rate_limiter("ct"),
        task_id=task_id,
        scope_version=scope.scope_version,
        scope_confirmed=True,
        metadata={"scope_payload": scope.to_dict()},
    )

    action = {
        "action_id": _action_id(normalized_domain),
        "target": normalized_domain,
        "capability": "ct_search",
        "budget_kind": adapter.manifest.name,
    }
    result = await executor.execute(adapter, action, context)
    stored = _store_ct_observations(db, task_id, scope, result)
    certificates = sum(
        len(item.get("certificates", []))
        for item in result.observations
        if item.get("status") in {"observed", "empty"}
    )
    failovers = sum(1 for item in result.observations if item.get("failover"))
    if result.status == "completed":
        return CtRunResult("completed", stored, certificates, failovers=failovers)
    if result.status == "blocked":
        return CtRunResult("blocked", stored, certificates, error=result.error, failovers=failovers)
    return CtRunResult("failed", stored, certificates, error=result.error, failovers=failovers)
