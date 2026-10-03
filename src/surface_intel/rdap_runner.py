from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .adapters.base import AdapterResult
from .adapters.capabilities import CapabilityRegistry
from .adapters.rdap import TARGET_KINDS, RdapAdapter
from .execution import AdapterExecutor, ExecutionContext
from .policy.scope import Scope
from .state.database import Database
from .state.observations import store_unified_observations


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
    def metadata_for(payload: dict[str, Any]) -> dict[str, Any]:
        fallback = (payload.get("record") or {}).get("data_quality") == "whois_fallback"
        # Registration data describes who runs an authorized target; it can
        # never confirm attribution on its own - matching organization or
        # ASN names stay candidate relations for the R4 merge layer.
        return {
            "source_quality": "whois_fallback" if fallback else "direct",
            "license_status": "whois_text_unverified" if fallback else "public_rdap",
            "related_scope_status": (
                "not_applicable" if payload.get("status") == "not_found" else "candidate"
            ),
        }

    return store_unified_observations(
        db, task_id, scope, result, source="rdap_lookup", metadata_for=metadata_for,
    )


def _target_in_scope(scope: Scope, target: str, kind: str) -> bool:
    if kind == "domain":
        return scope.is_in_scope(target)
    if kind == "ip":
        return scope.is_in_scope(target)  # matches authorized_ips or authorized_cidrs
    if kind == "asn":
        return scope.is_in_scope(target)  # matches authorized_asns when present
    return False


def _asn_merge_binding(db: Database, task_id: str, asn_value: str) -> tuple[str, str] | None:
    """Merge-layer binding basis for an ASN lookup (the R4 promise from R3.3).

    A binding exists when the task's merged relations tie the ASN to a
    scope-confirmed asset via an `rdap_asn` relation - i.e. the ASN was
    surfaced by an authorized target's own registration record, not by
    guesswork. Returns (relation_id, anchor_value).
    """
    rows = list(db.rows(
        """SELECT r.relation_id, src.normalized_value AS anchor_value
           FROM asset_relation r
           JOIN asset src ON src.asset_id = r.from_asset_id
           JOIN asset dst ON dst.asset_id = r.to_asset_id
           WHERE r.task_id=? AND r.relation_type='rdap_asn'
             AND src.scope_status='confirmed'
             AND dst.asset_type='asn' AND dst.normalized_value=?
           ORDER BY r.rowid LIMIT 1""",
        (task_id, asn_value),
    ))
    if not rows:
        return None
    return rows[0]["relation_id"], rows[0]["anchor_value"]


async def run_rdap_lookup(
    db: Database,
    client: Any,
    *,
    task_id: str,
    scope: Scope,
    target: str,
    kind: str,
) -> RdapRunResult:
    """Passive registration lookup for one authorized domain, IP or ASN, one action.

    Gates before any client call: confirmed scope and target authorization.
    Domains and IPs must be in scope. ASN lookups are authorized by either an
    explicit `authorized_asns` scope entry or an R4 merge binding (`rdap_asn`
    relation from a scope-confirmed asset); unbound ASNs are refused with
    `asn_binding_not_found`. Registration records are stored as observations
    whose attribution relations stay `candidate`: organization or ASN equality
    never confirms ownership.
    """
    normalized_target = target.strip().rstrip(".").lower()
    normalized_kind = str(kind).strip().lower()
    if normalized_kind not in TARGET_KINDS:
        return RdapRunResult("blocked", 0, error="invalid_target_kind")
    if not scope.confirmed:
        return RdapRunResult("blocked", 0, error="scope_not_confirmed")

    action = {
        "action_id": _action_id(normalized_kind, normalized_target),
        "target": normalized_target,
        "kind": normalized_kind,
    }
    if not _target_in_scope(scope, normalized_target, normalized_kind):
        if normalized_kind != "asn":
            return RdapRunResult("blocked", 0, error="target_out_of_scope")
        # ASN: authorized either by explicit authorized_asns scope entries
        # (already in scope) or by an R4 merge binding from a confirmed asset.
        binding = _asn_merge_binding(db, task_id, normalized_target.removeprefix("as"))
        if binding is None:
            return RdapRunResult("blocked", 0, error="asn_binding_not_found")
        relation_id, anchor_value = binding
        # The scope-checked action target stays the in-scope anchor; the ASN
        # rides `lookup_target`. Both are visible in the adapter_call audit.
        action["target"] = anchor_value
        action["lookup_target"] = normalized_target
        action["scope_basis"] = f"merge_binding:{relation_id}"

    adapter = RdapAdapter(client)
    registry = CapabilityRegistry([adapter.manifest])
    executor = AdapterExecutor(db, registry)
    context = ExecutionContext(
        task_id=task_id,
        scope_version=scope.scope_version,
        scope_confirmed=True,
        metadata={"scope_payload": scope.to_dict()},
    )

    action["capability"] = "rdap_lookup"
    action["budget_kind"] = adapter.manifest.name
    result = await executor.execute(adapter, action, context)
    stored = _store_rdap_observations(db, task_id, scope, result)
    if result.status == "completed":
        return RdapRunResult("completed", stored)
    if result.status == "blocked":
        return RdapRunResult("blocked", stored, error=result.error)
    return RdapRunResult("failed", stored, error=result.error)
