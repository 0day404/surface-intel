from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .adapters.base import AdapterResult
from .adapters.capabilities import CapabilityRegistry
from .adapters.ct import CertificateTransparencyAdapter
from .execution import AdapterExecutor, ExecutionContext
from .policy.scope import Scope
from .redaction import redact
from .state.database import Database


@dataclass(frozen=True)
class CtRunResult:
    status: str
    observations: int
    certificates: int
    error: str | None = None


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


def _action_id(domain: str) -> str:
    digest = hashlib.sha256(domain.encode()).hexdigest()[:12]
    return f"ct-{digest}"


def _store_ct_observations(
    db: Database,
    task_id: str,
    scope: Scope,
    result: AdapterResult,
) -> int:
    observed_at = datetime.now(timezone.utc).isoformat()
    stored = 0
    with db.connect() as connection:
        for item in result.observations:
            payload = redact(dict(item))
            payload["scope_version"] = scope.scope_version
            payload["observed_at"] = observed_at
            payload["source_quality"] = "direct"
            payload["license_status"] = "public_ct_log"
            payload["related_scope_status"] = classify_certificates_scope(
                scope, list(payload.get("certificates", []))
            )
            raw = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            evidence_id = f"evidence-{uuid.uuid4().hex[:16]}"
            connection.execute(
                """INSERT INTO evidence(evidence_id,task_id,source,summary,sha256,redaction_status)
                   VALUES(?,?,?,?,?,?)""",
                (evidence_id, task_id, "ct_log", raw[:500],
                 hashlib.sha256(raw.encode()).hexdigest(), "redacted"),
            )
            connection.execute(
                """INSERT INTO observation(observation_id,task_id,source,target,payload_json,evidence_id)
                   VALUES(?,?,?,?,?,?)""",
                (f"observation-{uuid.uuid4().hex[:16]}", task_id, "ct_log",
                 str(payload.get("target", "")), raw, evidence_id),
            )
            stored += 1
    return stored


async def run_ct_search(
    db: Database,
    client: Any,
    *,
    task_id: str,
    scope: Scope,
    domain: str,
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

    adapter = CertificateTransparencyAdapter(client)
    registry = CapabilityRegistry([adapter.manifest])
    executor = AdapterExecutor(db, registry)
    context = ExecutionContext(
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
    if result.status == "completed":
        return CtRunResult("completed", stored, certificates)
    if result.status == "blocked":
        return CtRunResult("blocked", stored, certificates, error=result.error)
    return CtRunResult("failed", stored, certificates, error=result.error)
