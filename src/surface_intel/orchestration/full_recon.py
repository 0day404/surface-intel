"""Tested full-domain passive recon orchestration (retrospective P1-5).

Promoted from the ad-hoc runtime driver that accumulated four authoring
errors. The discovery chain - FOFA batches -> CT SANs -> DNS A records ->
RDAP on capped IPs -> merge + architecture - is one maintained function
with injected clients, so every future full-recon run shares the same
tested path instead of copy-pasted driver code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..adapters.base import TaskContext
from ..architecture import annotate_architecture
from ..ct_runner import run_ct_search
from ..dns_runner import run_dns_lookup
from ..fofa_runner import run_fofa_query_batches
from ..merger import merge_task_observations
from ..policy.scope import Scope
from ..rdap_runner import run_rdap_lookup
from ..state.database import Database

# discovery caps: passive recon must stay bounded even on large scopes
MAX_DNS_HOSTS = 20
DEFAULT_MAX_RDAP_IPS = 5


@dataclass
class FullReconResult:
    status: str
    task_id: str
    fofa_batches: list[dict[str, Any]] = field(default_factory=list)
    ct: dict[str, Any] = field(default_factory=dict)
    dns: dict[str, str] = field(default_factory=dict)
    rdap: dict[str, dict[str, Any]] = field(default_factory=dict)
    # A3: RDAP refusals are easy to miss inside the per-IP rdap map - the
    # blocked subset is surfaced at the top level for the operator
    rdap_blocked: list[dict[str, str]] = field(default_factory=list)
    scope_amendment: dict[str, Any] | None = None
    discovered_hosts: list[str] = field(default_factory=list)
    discovered_ips: list[str] = field(default_factory=list)
    merge: dict[str, Any] = field(default_factory=dict)
    assets_by_type: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    skipped: list[dict[str, Any]] = field(default_factory=list)


def _hosts_from_fofa(db: Database, task_id: str) -> tuple[set[str], set[str]]:
    hosts: set[str] = set()
    ips: set[str] = set()
    for row in db.rows(
        "SELECT payload_json FROM observation WHERE task_id=? AND source='fofa_mcp'",
        (task_id,),
    ):
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        raw_host = str(payload.get("host", "") or "").split("/")[0].strip().lower()
        if raw_host and ":" not in raw_host and not _is_ip(raw_host):
            hosts.add(raw_host.rstrip("."))
        domain = str(payload.get("domain", "") or "").strip().lower().rstrip(".")
        if domain and ":" not in domain and not _is_ip(domain):
            hosts.add(domain)
        ip = payload.get("ip")
        if ip:
            ips.add(str(ip))
    return hosts, ips


def _sans_from_ct(db: Database, task_id: str) -> set[str]:
    sans: set[str] = set()
    for row in db.rows(
        "SELECT payload_json FROM observation WHERE task_id=? AND source='ct_log'",
        (task_id,),
    ):
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        for cert in payload.get("certificates", []):
            for san in cert.get("sans", []):
                cleaned = str(san).strip().lower().rstrip(".").lstrip("*.")
                if cleaned and "." in cleaned:
                    sans.add(cleaned)
    return sans


def _ips_from_dns_a(db: Database, task_id: str, host: str) -> set[str]:
    ips: set[str] = set()
    for row in db.rows(
        "SELECT payload_json FROM observation WHERE task_id=? AND source='dns_resolver' "
        "AND target=? AND json_extract(payload_json, '$.record_type')='A'",
        (task_id, host),
    ):
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        for value in payload.get("records", []):
            ips.add(str(value))
    return ips


def _is_ip(value: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(value.strip())
        return True
    except ValueError:
        return False


def _in_scope_host(scope: Scope, host: str) -> bool:
    return bool(host) and scope.is_in_scope(host)


async def run_full_passive_recon(
    db: Database,
    *,
    task_id: str,
    scope: Scope,
    fofa_client: Any,
    ct_client: Any,
    dns_client: Any,
    rdap_client: Any,
    fofa_query: str,
    fofa_size: int = 100,
    fofa_max_pages: int = 5,
    ct_domain: str | None = None,
    dns_hosts: list[str] | None = None,
    max_rdap_ips: int = DEFAULT_MAX_RDAP_IPS,
    max_dns_hosts: int = MAX_DNS_HOSTS,
    auto_amend_discovered_ips: bool = False,
) -> FullReconResult:
    """Run the complete passive discovery chain for one authorized scope.

    Order mirrors the plan: FOFA batches first (broadest coverage), CT on the
    requested domain, DNS on every discovered/known host (A records feed the
    IP set), RDAP on the first `max_rdap_ips` discovered addresses, then the
    merge and architecture layers over everything collected. All gates live
    in the per-source runners; this function only sequences them and stays
    passive by construction.
    """
    result = FullReconResult(status="completed", task_id=task_id)
    if not scope.confirmed:
        result.status = "blocked"
        result.error = "scope_not_confirmed"
        return result

    context = TaskContext(task_id, scope.scope_version, {"scope_confirmed": True})

    # 1) FOFA batches (idempotent per batch fingerprint)
    fofa_results = await run_fofa_query_batches(
        db, fofa_client, task_id=task_id, scope_version=scope.scope_version,
        query=fofa_query, context=context, size=fofa_size, max_pages=fofa_max_pages,
    )
    result.fofa_batches = [asdict_batch(r) for r in fofa_results]

    # 2) harvest hosts and IPs from the FOFA observations
    hosts, discovered_ips = _hosts_from_fofa(db, task_id)

    # 3) CT on the requested domain; SANs extend the host set
    if ct_domain:
        ct_run = await run_ct_search(db, ct_client, task_id=task_id, scope=scope,
                                     domain=ct_domain)
        result.ct = {"status": ct_run.status, "observations": ct_run.observations,
                     "certificates": ct_run.certificates, "error": ct_run.error,
                     "failovers": ct_run.failovers}
        for san in _sans_from_ct(db, task_id):
            if _in_scope_host(scope, san):
                hosts.add(san)

    # known scope anchors are always resolved even if FOFA/CT missed them;
    # caller-supplied dns_hosts join the resolution set (retrospective P1-5:
    # the original driver ignored this parameter)
    for domain in scope.authorized_domains:
        hosts.add(domain.strip().lower().rstrip(".").lstrip("*.").lstrip("*"))
    for host in dns_hosts or []:
        cleaned = host.strip().lower().rstrip(".")
        if cleaned:
            hosts.add(cleaned)

    # 4) DNS on capped host set; A records extend the IP set. Which host
    # revealed which IP is tracked for the optional auto-amendment (A3).
    dns_hosts_final = sorted(h for h in hosts if h)[:max_dns_hosts]
    result.discovered_hosts = dns_hosts_final
    dns_revealed: dict[str, set[str]] = {}
    for host in dns_hosts_final:
        dns_run = await run_dns_lookup(db, dns_client, task_id=task_id, scope=scope,
                                       domain=host)
        result.dns[host] = dns_run.status
        for ip in _ips_from_dns_a(db, task_id, host):
            discovered_ips.add(ip)
            dns_revealed.setdefault(ip, set()).add(host)

    # 4.5) A3: optionally self-amend with the IPs the authorized domains' own
    # A records revealed. Explicit opt-in, audited (source + revealed_by), and
    # the refreshed scope feeds the RDAP stage below. FOFA-harvested IPs stay
    # out - third-party index data never expands the scope by itself.
    pending = sorted(ip for ip in dns_revealed if not scope.is_in_scope(ip))
    if auto_amend_discovered_ips and pending:
        from ..policy.scope_amend import apply_scope_amendment

        outcome = apply_scope_amendment(
            db, task_id, base_payload=scope.to_dict(), add_ips=tuple(pending),
            reason="IP revealed by A records of authorized domains during recon",
            source="auto_recon_dns_a",
            event_extra={"revealed_by": {ip: sorted(dns_revealed[ip]) for ip in pending}},
        )
        scope = Scope.from_dict(outcome.payload)
        result.scope_amendment = {
            "new_scope_version": outcome.new_scope_version,
            "added_ips": list(outcome.added_ips),
            "source": "auto_recon_dns_a",
        }

    # 5) RDAP on capped IP set
    rdap_targets = sorted(discovered_ips)[:max_rdap_ips]
    result.discovered_ips = rdap_targets
    for ip in rdap_targets:
        rdap_run = await run_rdap_lookup(db, rdap_client, task_id=task_id, scope=scope,
                                         target=ip, kind="ip")
        result.rdap[ip] = {"status": rdap_run.status, "error": rdap_run.error}
        if rdap_run.status == "blocked":
            result.rdap_blocked.append({"ip": ip, "error": rdap_run.error or "blocked"})

    # 6) merge + architecture over everything collected
    merge_run = merge_task_observations(db, task_id, scope)
    annotate_architecture(db, task_id)
    result.merge = {
        "observations_processed": merge_run.observations_processed,
        "assets_created": merge_run.assets_created,
        "assets_updated": merge_run.assets_updated,
        "relations_created": merge_run.relations_created,
        "conflicts": merge_run.conflicts_created,
    }
    result.skipped = list(merge_run.skipped)
    result.assets_by_type = {
        row["asset_type"]: row["count"]
        for row in db.rows(
            "SELECT asset_type, COUNT(*) AS count FROM asset WHERE task_id=? GROUP BY asset_type",
            (task_id,),
        )
    }
    return result


def asdict_batch(batch_result: Any) -> dict[str, Any]:
    """Batch summary without importing the runner's private dataclass here."""
    return {
        "status": batch_result.status,
        "observations": batch_result.observations,
        "pages": batch_result.pages,
        "batch_id": batch_result.batch_id,
        "consumed_fpoints": batch_result.consumed_fpoints,
        "error": batch_result.error,
    }
