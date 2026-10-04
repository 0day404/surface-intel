from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from ..adapters.base import AdapterResult
from ..policy.scope import Scope
from ..redaction import placeholderize, redact
from .database import Database

# Keys every passive-source observation must carry, regardless of adapter.
# Source-specific payloads add their own fields on top; the merge layer (R4)
# relies on this contract being uniform across dns_resolver / ct_log /
# rdap_lookup.
UNIFIED_METADATA_KEYS = (
    "scope_version",
    "observed_at",
    "source_quality",
    "license_status",
    "related_scope_status",
)


def store_redaction_pairs(db: Database, connection, task_id: str,
                          pairs: list[tuple[str, str]]) -> None:
    """Persist email placeholder -> plaintext pairs (C3) in the caller's
    transaction. The map stays in the local task.sqlite; deliverables carry
    placeholders only until `report --reveal-emails` is passed."""
    for placeholder, plaintext in pairs:
        connection.execute(
            "INSERT OR IGNORE INTO redaction_map(task_id,placeholder,plaintext) VALUES(?,?,?)",
            (task_id, placeholder, plaintext),
        )


def store_unified_observations(
    db: Database,
    task_id: str,
    scope: Scope,
    result: AdapterResult,
    *,
    source: str,
    metadata_for: Callable[[dict[str, Any]], dict[str, Any]],
) -> int:
    """Persist one adapter result as redacted evidence + unified observations.

    `metadata_for` receives each redacted observation payload and returns the
    source-specific `{source_quality, license_status, related_scope_status}`
    triple; scope binding and timestamps are added here so every passive
    source carries the same metadata contract. Raw payloads are redacted
    before any write and never reach the logging pipeline; email addresses
    are additionally placeholderized with the plaintext kept in redaction_map
    (C3) - the evidence sha256 covers the placeholderized payload.
    """
    observed_at = datetime.now(timezone.utc).isoformat()
    stored = 0
    with db.connect() as connection:
        for item in result.observations:
            payload, email_pairs = placeholderize(redact(dict(item)), task_id)
            store_redaction_pairs(db, connection, task_id, email_pairs)
            payload["scope_version"] = scope.scope_version
            payload["observed_at"] = observed_at
            payload.update(metadata_for(payload))
            raw = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            evidence_id = f"evidence-{uuid.uuid4().hex[:16]}"
            connection.execute(
                """INSERT INTO evidence(evidence_id,task_id,source,summary,sha256,redaction_status)
                   VALUES(?,?,?,?,?,?)""",
                (evidence_id, task_id, source, raw[:500],
                 hashlib.sha256(raw.encode()).hexdigest(), "redacted"),
            )
            connection.execute(
                """INSERT INTO observation(observation_id,task_id,source,target,payload_json,evidence_id)
                   VALUES(?,?,?,?,?,?)""",
                (f"observation-{uuid.uuid4().hex[:16]}", task_id, source,
                 str(payload.get("target", "")), raw, evidence_id),
            )
            stored += 1
    return stored
