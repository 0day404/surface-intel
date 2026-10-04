from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from .adapters.base import AdapterResult
from .adapters.capabilities import CapabilityRegistry
from .adapters.safe_http import SafeHttpAdapter
from .execution import AdapterExecutor, ExecutionContext
from .pacing import configured_rate_limiter
from .policy.scope import Scope
from .state.database import Database
from .states import ActiveLevel

# Plan R4 acceptance: CDN/WAF/LB/cloud-hosting signals and third-party markers
# block active probing and generate a review item. `unknown` assets and
# in-scope targets without an asset stay allowed - passivity never confirms.
BLOCKING_ARCHITECTURE_STATUSES = {
    "third_party",
    "cdn_candidate", "cdn_likely",
    "waf_candidate", "waf_likely",
    "lb_candidate", "lb_likely",
    "cloud_hosting_candidate", "cloud_hosting_likely",
}


@dataclass(frozen=True)
class SafeHttpRunResult:
    status: str
    observations: int
    error: str | None = None
    review_created: bool = False
    # A4: set when --allow-hosted-third-party overrode a third_party block
    override_used: bool = False


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").strip().lower().rstrip(".")


def _action_id(url: str, method: str) -> str:
    digest = hashlib.sha256(f"{method}:{url}".encode()).hexdigest()[:12]
    return f"http-{digest}"


def _architecture_block(db: Database, task_id: str, host: str) -> str | None:
    """The blocking architecture status for a host, if any (R4 gate)."""
    for asset_type in ("domain", "ip"):
        row = next(db.rows(
            """SELECT architecture_status FROM asset
               WHERE task_id=? AND asset_type=? AND normalized_value=?
                 AND architecture_status IN ({})""".format(
                ",".join("?" * len(BLOCKING_ARCHITECTURE_STATUSES))),
            (task_id, asset_type, host, *sorted(BLOCKING_ARCHITECTURE_STATUSES)),
        ), None)
        if row is not None:
            return row["architecture_status"]
    return None


def _ensure_review_item(db: Database, task_id: str, host: str, architecture_status: str) -> bool:
    """Idempotent review item (gap row) for a blocked target; the reason
    carries the remediation paths so the queue item is actionable (A4)."""
    reason = (f"architecture_blocked:{architecture_status}:{host} "
              f"(remedy: scope-amend --add-ips 后重试, 或 --allow-hosted-third-party)")
    existing = next(db.rows(
        "SELECT gap_id FROM gap WHERE task_id=? AND reason=?", (task_id, reason),
    ), None)
    if existing is not None:
        return False
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO gap(gap_id,task_id,priority,reason,status) VALUES(?,?,?,?,'open')",
            (f"gap-{uuid.uuid4().hex[:16]}", task_id, "high", reason),
        )
    return True


def _ensure_override_review_item(db: Database, task_id: str, host: str,
                                 architecture_status: str) -> bool:
    """A4: an explicit --allow-hosted-third-party bypass is itself recorded
    as a review item, so an override never happens silently."""
    reason = f"architecture_override:{architecture_status}:{host}"
    existing = next(db.rows(
        "SELECT gap_id FROM gap WHERE task_id=? AND reason=?", (task_id, reason),
    ), None)
    if existing is not None:
        return False
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO gap(gap_id,task_id,priority,reason,status) VALUES(?,?,?,?,'open')",
            (f"gap-{uuid.uuid4().hex[:16]}", task_id, "medium", reason),
        )
    return True


def _store_http_observation(db: Database, task_id: str, scope: Scope, result: AdapterResult) -> int:
    observed_at = datetime.now(timezone.utc).isoformat()
    stored = 0
    redirect_hosts = [
        (_host_of(item.get("url", "")))
        for item in (result.observations[0].get("redirect_chain", []) if result.observations else [])
    ]
    redirect_hosts = [host for host in redirect_hosts if host]
    if not redirect_hosts:
        related = "not_applicable"
    elif all(scope.is_in_scope(host) for host in redirect_hosts):
        related = "candidate"
    else:
        related = "needs_review"
    with db.connect() as connection:
        for item in result.observations:
            from .redaction import placeholderize, redact
            from .state.observations import store_redaction_pairs

            payload, email_pairs = placeholderize(redact(dict(item)), task_id)
            store_redaction_pairs(db, connection, task_id, email_pairs)
            payload["scope_version"] = scope.scope_version
            payload["observed_at"] = observed_at
            payload["source_quality"] = "direct"
            payload["license_status"] = "public_http"
            payload["related_scope_status"] = related
            raw = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            evidence_id = f"evidence-{uuid.uuid4().hex[:16]}"
            connection.execute(
                """INSERT INTO evidence(evidence_id,task_id,source,summary,sha256,redaction_status)
                   VALUES(?,?,?,?,?,?)""",
                (evidence_id, task_id, "safe_http", raw[:500],
                 hashlib.sha256(raw.encode()).hexdigest(), "redacted"),
            )
            connection.execute(
                """INSERT INTO observation(observation_id,task_id,source,target,payload_json,evidence_id)
                   VALUES(?,?,?,?,?,?)""",
                (f"observation-{uuid.uuid4().hex[:16]}", task_id, "safe_http",
                 str(payload.get("target", "")), raw, evidence_id),
            )
            stored += 1
    return stored


async def run_http_observe(
    db: Database,
    client: Any,
    *,
    task_id: str,
    scope: Scope,
    url: str,
    method: str = "GET",
    allow_hosted_third_party: bool = False,
) -> SafeHttpRunResult:
    """One gated low-impact HTTP observation (plan R6.1).

    Gates before any network activity: confirmed scope, active testing
    authorization, target host in scope, and the R4 architecture gate
    (CDN/WAF/LB/cloud/third-party signals block probing and create a review
    item). Redirects are followed only while they stay on confirmed hosts;
    bodies are reduced to digests client-side and never persisted.
    """
    url = url.strip()
    host = _host_of(url)
    if not scope.confirmed:
        return SafeHttpRunResult("blocked", 0, error="scope_not_confirmed")
    if scope.active_testing_level == ActiveLevel.PASSIVE_ONLY or not scope.active_testing_allowed:
        return SafeHttpRunResult("blocked", 0, error="active_testing_not_authorized")
    if not host or not scope.is_in_scope(host):
        return SafeHttpRunResult("blocked", 0, error="target_out_of_scope")
    blocking = _architecture_block(db, task_id, host)
    override_used = False
    if blocking is not None:
        # A4: only the plain third_party status lifts via the explicit flag -
        # cdn/waf/lb/cloud_hosting signals stay blocking either way
        if allow_hosted_third_party and blocking == "third_party":
            _ensure_override_review_item(db, task_id, host, blocking)
            override_used = True
        else:
            review_created = _ensure_review_item(db, task_id, host, blocking)
            return SafeHttpRunResult("blocked", 0, error=f"architecture_blocked:{blocking}",
                                     review_created=review_created)

    allowed_hosts = tuple(scope.authorized_domains) + tuple(scope.authorized_ips) + tuple(scope.authorized_targets)
    method = method.upper()

    def transport(target_url: str) -> dict[str, Any]:
        # the adapter's transport contract is transport(url); method and the
        # redirect allowlist ride the closure
        return client.observe(target_url, method=method, allowed_hosts=allowed_hosts)

    adapter = SafeHttpAdapter(transport)
    registry = CapabilityRegistry([adapter.manifest])
    executor = AdapterExecutor(db, registry)
    context = ExecutionContext(
        rate_limiter=configured_rate_limiter("safe_http"),
        task_id=task_id,
        scope_version=scope.scope_version,
        scope_confirmed=True,
        active_level=scope.active_testing_level.value,
        metadata={
            "scope_payload": scope.to_dict(),
            "allowed_methods": ("GET", "HEAD"),
            "allowed_hosts": allowed_hosts,
        },
    )
    action = {
        "action_id": _action_id(url, method.upper()),
        # the scope-checked target is the host; the full URL rides
        # lookup_target so the executor gate and the audit stay consistent
        "target": host,
        "lookup_target": url,
        "method": method.upper(),
        "capability": "http_observation",
        "budget_kind": adapter.manifest.name,
        "phase": "safe_http",
    }
    result = await executor.execute(adapter, action, context)
    stored = _store_http_observation(db, task_id, scope, result)
    if result.status == "completed":
        return SafeHttpRunResult("completed", stored, override_used=override_used)
    if result.status == "blocked":
        return SafeHttpRunResult("blocked", stored, error=result.error,
                                 override_used=override_used)
    return SafeHttpRunResult("failed", stored, error=result.error,
                             override_used=override_used)
