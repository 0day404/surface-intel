from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from .adapters.capabilities import CapabilityRegistry
from .adapters.playwright import PlaywrightAdapter
from .execution import AdapterExecutor, ExecutionContext
from .policy.scope import Scope
from .safe_http_runner import _architecture_block, _ensure_review_item
from .state.database import Database


@dataclass(frozen=True)
class PlaywrightRunResult:
    status: str
    observations: int
    error: str | None = None
    review_created: bool = False


def _action_id(url: str) -> str:
    digest = hashlib.sha256(url.encode()).hexdigest()[:12]
    return f"browser-{digest}"


def _host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").strip().lower().rstrip(".")


def _store_browser_observation(db: Database, task_id: str, scope: Scope, result) -> int:
    observed_at = datetime.now(timezone.utc).isoformat()
    stored = 0
    with db.connect() as connection:
        for item in result.observations:
            from .redaction import redact

            payload = redact(dict(item))
            payload["scope_version"] = scope.scope_version
            payload["observed_at"] = observed_at
            payload["source_quality"] = "direct"
            payload["license_status"] = "public_http"
            third_party = payload.get("third_party_origins") or []
            if third_party:
                payload["related_scope_status"] = "needs_review"
            else:
                payload["related_scope_status"] = "candidate"
            raw = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            evidence_id = f"evidence-{uuid.uuid4().hex[:16]}"
            connection.execute(
                """INSERT INTO evidence(evidence_id,task_id,source,summary,sha256,redaction_status)
                   VALUES(?,?,?,?,?,?)""",
                (evidence_id, task_id, "playwright_mcp", raw[:500],
                 hashlib.sha256(raw.encode()).hexdigest(), "redacted"),
            )
            connection.execute(
                """INSERT INTO observation(observation_id,task_id,source,target,payload_json,evidence_id)
                   VALUES(?,?,?,?,?,?)""",
                (f"observation-{uuid.uuid4().hex[:16]}", task_id, "playwright_mcp",
                 str(payload.get("page_url", payload.get("target", ""))), raw, evidence_id),
            )
            stored += 1
    return stored


async def run_browser_observe(
    db: Database,
    client: Any,
    *,
    task_id: str,
    scope: Scope,
    url: str,
    max_requests: int = 100,
) -> PlaywrightRunResult:
    """One gated public-page browser observation (plan R6.3).

    Gates: confirmed scope, active testing authorization, host in scope, R4
    architecture gate. The client enforces the browser scope object (network
    summary capped, third-party origins recorded but never followed, headers/
    cookies/post-data dropped before evidence). Login, state change, downloads
    and uploads are refused by the adapter contract, not just policy.
    """
    url = url.strip()
    host = _host_of(url)
    if not scope.confirmed:
        return PlaywrightRunResult("blocked", 0, error="scope_not_confirmed")
    if scope.active_testing_level.value == "passive_only" or not scope.active_testing_allowed:
        return PlaywrightRunResult("blocked", 0, error="active_testing_not_authorized")
    if not host or not scope.is_in_scope(host):
        return PlaywrightRunResult("blocked", 0, error="target_out_of_scope")
    blocking = _architecture_block(db, task_id, host)
    if blocking is not None:
        review_created = _ensure_review_item(db, task_id, host, blocking)
        return PlaywrightRunResult("blocked", 0, error=f"architecture_blocked:{blocking}",
                                   review_created=review_created)

    scope_domains = tuple(scope.authorized_domains) + tuple(scope.authorized_targets)
    adapter = PlaywrightAdapter(
        lambda tool, arguments: client.observe_public_page(
            arguments.get("url", ""), scope_domains=scope_domains, max_requests=max_requests)
        if tool == "observe_public_page"
        else _health_of(client)
    )
    registry = CapabilityRegistry([adapter.manifest])
    executor = AdapterExecutor(db, registry)
    context = ExecutionContext(
        task_id=task_id,
        scope_version=scope.scope_version,
        scope_confirmed=True,
        active_level=scope.active_testing_level.value,
        metadata={
            "scope_payload": scope.to_dict(),
            "allowed_domains": tuple(scope.authorized_domains),
        },
    )
    action = {
        "action_id": _action_id(url),
        "target": host,
        "lookup_target": url,
        "capability": "browser_navigation",
        "budget_kind": adapter.manifest.name,
        "phase": "observing_browser",
    }
    result = await executor.execute(adapter, action, context)
    stored = _store_browser_observation(db, task_id, scope, result)
    if result.status == "completed":
        return PlaywrightRunResult("completed", stored)
    if result.status == "blocked":
        return PlaywrightRunResult("blocked", stored, error=result.error)
    return PlaywrightRunResult("failed", stored, error=result.error)


async def _health_of(client: Any) -> dict[str, Any]:
    health = await client.health()
    return {"ok": health.get("ok"), "request_id": None, "error": health.get("error")}
