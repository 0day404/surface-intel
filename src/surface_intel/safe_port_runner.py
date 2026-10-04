from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from .adapters.capabilities import CapabilityRegistry
from .adapters.safe_port import SafePortAdapter
from .execution import AdapterExecutor, ExecutionContext
from .policy.scope import Scope
from .safe_http_runner import _ensure_review_item, _architecture_block
from .state.database import Database


@dataclass(frozen=True)
class SafePortRunResult:
    status: str
    observations: int
    error: str | None = None
    review_created: bool = False
    # A4: set when --allow-hosted-third-party overrode a third_party block
    override_used: bool = False


def _action_id(host: str, port: int) -> str:
    digest = hashlib.sha256(f"{host}:{port}".encode()).hexdigest()[:12]
    return f"port-{digest}"


def _store_port_observation(db: Database, task_id: str, scope: Scope, result) -> int:
    observed_at = datetime.now(timezone.utc).isoformat()
    stored = 0
    with db.connect() as connection:
        for item in result.observations:
            from .redaction import placeholderize, redact
            from .state.observations import store_redaction_pairs

            payload, email_pairs = placeholderize(redact(dict(item)), task_id)
            store_redaction_pairs(db, connection, task_id, email_pairs)
            payload["scope_version"] = scope.scope_version
            payload["observed_at"] = observed_at
            payload["source_quality"] = "direct"
            payload["license_status"] = "public_probe"
            # an open port on an authorized target is a candidate service
            # observation - it never proves ownership (plan R6.2)
            payload["related_scope_status"] = (
                "candidate" if payload.get("transport_status", payload.get("status")) == "open"
                else "not_applicable"
            )
            raw = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            evidence_id = f"evidence-{uuid.uuid4().hex[:16]}"
            connection.execute(
                """INSERT INTO evidence(evidence_id,task_id,source,summary,sha256,redaction_status)
                   VALUES(?,?,?,?,?,?)""",
                (evidence_id, task_id, "safe_port", raw[:500],
                 hashlib.sha256(raw.encode()).hexdigest(), "redacted"),
            )
            connection.execute(
                """INSERT INTO observation(observation_id,task_id,source,target,payload_json,evidence_id)
                   VALUES(?,?,?,?,?,?)""",
                (f"observation-{uuid.uuid4().hex[:16]}", task_id, "safe_port",
                 str(payload.get("target", "")), raw, evidence_id),
            )
            stored += 1
    return stored


async def run_port_check(
    db: Database,
    client: Any,
    *,
    task_id: str,
    scope: Scope,
    target: str,
    port: int,
    allow_hosted_third_party: bool = False,
) -> SafePortRunResult:
    """One gated known-port verification (plan R6.2).

    The port must be explicitly listed in the scope's `allowed_ports` - an
    empty list authorizes nothing, and no range/adjacent expansion exists.
    Architecture blocking (CDN/third-party/shared-cloud signals) applies as
    for HTTP probing and creates a review item. An open port never confirms
    ownership or unlocks higher-level probing.
    """
    host = target.strip().lower().rstrip(".")
    port = int(port)
    if not scope.confirmed:
        return SafePortRunResult("blocked", 0, error="scope_not_confirmed")
    if scope.active_testing_level.value == "passive_only" or not scope.active_testing_allowed:
        return SafePortRunResult("blocked", 0, error="active_testing_not_authorized")
    if not host or not scope.is_in_scope(host):
        return SafePortRunResult("blocked", 0, error="target_out_of_scope")
    if port not in {int(item) for item in scope.allowed_ports}:
        return SafePortRunResult("blocked", 0, error="port_not_authorized")
    blocking = _architecture_block(db, task_id, host)
    override_used = False
    if blocking is not None:
        # A4: only the plain third_party status lifts via the explicit flag -
        # cdn/waf/lb/cloud_hosting signals stay blocking either way
        if allow_hosted_third_party and blocking == "third_party":
            from .safe_http_runner import _ensure_override_review_item

            _ensure_override_review_item(db, task_id, host, blocking)
            override_used = True
        else:
            review_created = _ensure_review_item(db, task_id, host, blocking)
            return SafePortRunResult("blocked", 0, error=f"architecture_blocked:{blocking}",
                                     review_created=review_created)

    adapter = SafePortAdapter(client.probe)
    registry = CapabilityRegistry([adapter.manifest])
    executor = AdapterExecutor(db, registry)
    context = ExecutionContext(
        task_id=task_id,
        scope_version=scope.scope_version,
        scope_confirmed=True,
        active_level=scope.active_testing_level.value,
        metadata={
            "scope_payload": scope.to_dict(),
            "allowed_ports": tuple(scope.allowed_ports),
        },
    )
    action = {
        "action_id": _action_id(host, port),
        "target": host,
        "port": port,
        "capability": "known_port_verification",
        "budget_kind": adapter.manifest.name,
        "phase": "safe_port",
    }
    result = await executor.execute(adapter, action, context)
    stored = _store_port_observation(db, task_id, scope, result)
    if result.status == "completed":
        return SafePortRunResult("completed", stored, override_used=override_used)
    if result.status == "blocked":
        return SafePortRunResult("blocked", stored, error=result.error,
                                 override_used=override_used)
    return SafePortRunResult("failed", stored, error=result.error,
                             override_used=override_used)
