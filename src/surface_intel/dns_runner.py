from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from typing import Any

from .adapters.base import AdapterResult
from .adapters.capabilities import CapabilityRegistry
from .adapters.dns import DNS_RECORD_TYPES, DnsAdapter
from .execution import AdapterExecutor, ExecutionContext
from .policy.scope import Scope
from .state.database import Database
from .state.observations import store_unified_observations


@dataclass(frozen=True)
class DnsRunResult:
    status: str
    observations: int
    queries: int
    error: str | None = None


def classify_records_scope(scope: Scope, record_type: str, records: list[str]) -> str:
    """Resolved values only become 'confirmed' when independently authorized.

    DNS answers about an authorized name never grant scope to the values they
    contain: A/AAAA values must fall inside authorized IPs/CIDRs, CNAME/NS/MX/
    SOA targets must themselves be in scope. TXT payloads cannot authorize
    anything and are not evaluated.
    """
    if record_type == "TXT" or not records:
        return "not_applicable"
    if record_type in {"A", "AAAA"}:
        addresses = []
        for value in records:
            try:
                addresses.append(ipaddress.ip_address(value.strip()))
            except ValueError:
                return "candidate"
        for address in addresses:
            in_cidr = any(address in ipaddress.ip_network(cidr) for cidr in scope.authorized_cidrs)
            in_list = address in (ipaddress.ip_address(item) for item in scope.authorized_ips if _is_ip(item))
            if not (in_cidr or in_list):
                return "candidate"
        return "confirmed"
    for value in records:
        name = value.split(" ", 1)[-1].strip().rstrip(".").lower()
        if not scope.is_in_scope(name):
            return "candidate"
    return "confirmed"


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _action_id(domain: str, record_type: str) -> str:
    digest = hashlib.sha256(domain.encode()).hexdigest()[:12]
    return f"dns-{digest}-{record_type.lower()}"


def _store_dns_observations(
    db: Database,
    task_id: str,
    scope: Scope,
    result: AdapterResult,
) -> int:
    def metadata_for(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_quality": "direct",
            "license_status": "public_protocol",
            "related_scope_status": classify_records_scope(
                scope, str(payload.get("record_type", "")), list(payload.get("records", []))
            ),
        }

    return store_unified_observations(
        db, task_id, scope, result, source="dns_resolver", metadata_for=metadata_for,
    )


async def run_dns_lookup(
    db: Database,
    client: Any,
    *,
    task_id: str,
    scope: Scope,
    domain: str,
    record_types: list[str] | None = None,
) -> DnsRunResult:
    """Passive DNS lookup for one authorized name, one action per record type.

    Gates before any resolver call: confirmed scope and target in scope. Each
    record type is an isolated action through the unified executor, so one
    failing type never discards the others. Resolved values are never followed
    recursively: CNAME/third-party targets stay `candidate`.
    """
    normalized_domain = domain.strip().rstrip(".").lower()
    types = [str(item).upper() for item in (record_types or list(DNS_RECORD_TYPES))]
    if not scope.confirmed:
        return DnsRunResult("blocked", 0, 0, error="scope_not_confirmed")
    if not scope.is_in_scope(normalized_domain):
        return DnsRunResult("blocked", 0, 0, error="target_out_of_scope")

    adapter = DnsAdapter(client)
    registry = CapabilityRegistry([adapter.manifest])
    executor = AdapterExecutor(db, registry)
    context = ExecutionContext(
        task_id=task_id,
        scope_version=scope.scope_version,
        scope_confirmed=True,
        metadata={"scope_payload": scope.to_dict()},
    )

    stored = 0
    ok_types = 0
    failed_types: list[str] = []
    last_error: str | None = None
    for record_type in types:
        action = {
            "action_id": _action_id(normalized_domain, record_type),
            "target": normalized_domain,
            "capability": "dns_resolve",
            "record_types": [record_type],
            "budget_kind": adapter.manifest.name,
        }
        result = await executor.execute(adapter, action, context)
        stored += _store_dns_observations(db, task_id, scope, result)
        if result.status == "completed":
            ok_types += 1
        else:
            failed_types.append(record_type)
            last_error = result.error
            if result.status == "blocked":
                # scope/budget/capability gates refuse further execution
                break

    total = len(types)
    if ok_types == total:
        return DnsRunResult("completed", stored, total)
    if ok_types > 0:
        return DnsRunResult("partial", stored, total, error=last_error)
    return DnsRunResult("failed", stored, total, error=last_error or "all_queries_failed")
