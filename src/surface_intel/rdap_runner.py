from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .adapters.base import AdapterResult
from .adapters.capabilities import CapabilityRegistry
from .adapters.rdap import TARGET_KINDS, RdapAdapter
from .execution import AdapterExecutor, ExecutionContext
from .policy.scope import Scope
from .redaction import redact
from .state.database import Database


@dataclass(frozen=True)
class RdapRunResult:
    status: str
    observations: int
    error: str | None = None


def _action_id(kind: str, target: str) -> str:
    digest = hashlib.sha256(f"{kind}:{target}".encode()).hexdigest()[:12]
    return f"rdap-{digest}"


def _store_rdap_observations(
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
            fallback = (payload.get("record") or {}).get("data_quality") == "whois_fallback"
            payload["source_quality"] = "whois_fallback" if fallback else "direct"
            payload["license_status"] = "whois_text_unverified" if fallback else "public_rdap"
            # Registration data describes who runs an authorized target; it can
            # never confirm attribution on its own - matching organization or
            # ASN names stay candidate relations for the R4 merge layer.
            payload["related_scope_status"] = (
                "not_applicable" if payload.get("status") == "not_found" else "candidate"
            )
            raw = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            evidence_id = f"evidence-{uuid.uuid4().hex[:16]}"
            connection.execute(
                """INSERT INTO evidence(evidence_id,task_id,source,summary,sha256,redaction_status)
                   VALUES(?,?,?,?,?,?)""",
                (evidence_id, task_id, "rdap_lookup", raw[:500],
                 hashlib.sha256(raw.encode()).hexdigest(), "redacted"),
            )
            connection.execute(
                """INSERT INTO observation(observation_id,task_id,source,target,payload_json,evidence_id)
                   VALUES(?,?,?,?,?,?)""",
                (f"observation-{uuid.uuid4().hex[:16]}", task_id, "rdap_lookup",
                 str(payload.get("target", "")), raw, evidence_id),
            )
            stored += 1
    return stored


def _target_in_scope(scope: Scope, target: str, kind: str) -> bool:
    if kind == "domain":
        return scope.is_in_scope(target)
    if kind == "ip":
        return scope.is_in_scope(target)  # matches authorized_ips or authorized_cidrs
    return False


async def run_rdap_lookup(
    db: Database,
    client: Any,
    *,
    task_id: str,
    scope: Scope,
    target: str,
    kind: str,
) -> RdapRunResult:
    """Passive registration lookup for one authorized domain or IP, one action.

    Gates before any client call: confirmed scope and target in scope (IPs
    must be listed or inside an authorized CIDR). ASN lookups are refused at
    the runner until R4 provides attribution binding — an ASN is never
    authorized by the scope document alone. Registration records are stored as
    observations whose attribution relations stay `candidate`: organization or
    ASN equality never confirms ownership.
    """
    normalized_target = target.strip().rstrip(".").lower()
    normalized_kind = str(kind).strip().lower()
    if normalized_kind not in TARGET_KINDS:
        return RdapRunResult("blocked", 0, error="invalid_target_kind")
    if not scope.confirmed:
        return RdapRunResult("blocked", 0, error="scope_not_confirmed")
    if normalized_kind == "asn":
        return RdapRunResult("blocked", 0, error="asn_scope_binding_deferred")
    if not _target_in_scope(scope, normalized_target, normalized_kind):
        return RdapRunResult("blocked", 0, error="target_out_of_scope")

    adapter = RdapAdapter(client)
    registry = CapabilityRegistry([adapter.manifest])
    executor = AdapterExecutor(db, registry)
    context = ExecutionContext(
        task_id=task_id,
        scope_version=scope.scope_version,
        scope_confirmed=True,
        metadata={"scope_payload": scope.to_dict()},
    )

    action = {
        "action_id": _action_id(normalized_kind, normalized_target),
        "target": normalized_target,
        "kind": normalized_kind,
        "capability": "rdap_lookup",
        "budget_kind": adapter.manifest.name,
    }
    result = await executor.execute(adapter, action, context)
    stored = _store_rdap_observations(db, task_id, scope, result)
    if result.status == "completed":
        return RdapRunResult("completed", stored)
    if result.status == "blocked":
        return RdapRunResult("blocked", stored, error=result.error)
    return RdapRunResult("failed", stored, error=result.error)
