from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .state.database import Database

BASELINE_PHASE = "baseline"


@dataclass(frozen=True)
class SnapshotDiff:
    scope_version: str | None
    baseline_scope_version: str | None
    scope_version_match: bool
    added: list[dict[str, Any]] = field(default_factory=list)
    disappeared: list[dict[str, Any]] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)
    reappeared: list[dict[str, Any]] = field(default_factory=list)
    unchanged: int = 0


def _asset_fingerprint(row) -> dict[str, Any]:
    return {
        "asset_type": row["asset_type"],
        "normalized_value": row["normalized_value"],
        "scope_status": row["scope_status"],
        "confidence": row["confidence"],
        "architecture_status": row["architecture_status"],
        "current_status": row["current_status"],
    }


def _current_assets(db: Database, task_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    rows = db.rows("SELECT * FROM asset WHERE task_id=?", (task_id,))
    return {(row["asset_type"], row["normalized_value"]): _asset_fingerprint(row)
            for row in rows}


def save_baseline(db: Database, task_id: str, scope_version: str) -> str:
    """Persist the current merged-asset set as the monitoring baseline.

    Baselines live as checkpoints with phase `baseline`, so they survive
    restarts and are covered by the existing recovery model.
    """
    assets = list(_current_assets(db, task_id).values())
    payload = {
        "scope_version": scope_version,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "assets": assets,
    }
    return create_baseline_checkpoint(db, task_id, payload)


def create_baseline_checkpoint(db: Database, task_id: str, payload: dict[str, Any]) -> str:
    checkpoint_id = f"checkpoint-{uuid.uuid4().hex[:16]}"
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO checkpoint(checkpoint_id,task_id,phase,status,payload_json) VALUES(?,?,?,?,?)",
            (checkpoint_id, task_id, BASELINE_PHASE, "valid",
             json.dumps(payload, ensure_ascii=True, sort_keys=True)),
        )
        connection.execute(
            "INSERT INTO event(event_id,task_id,event_type,payload_json) VALUES(?,?,?,?)",
            (f"event-{uuid.uuid4().hex[:16]}", task_id, "baseline_saved",
             json.dumps({"scope_version": payload.get("scope_version"),
                         "asset_count": len(payload.get("assets", []))}, ensure_ascii=True)),
        )
    return checkpoint_id


def latest_baseline(db: Database, task_id: str) -> dict[str, Any] | None:
    row = next(db.rows(
        """SELECT * FROM checkpoint WHERE task_id=? AND phase=? AND status='valid'
           ORDER BY created_at DESC LIMIT 1""",
        (task_id, BASELINE_PHASE),
    ), None)
    if row is None:
        return None
    value = dict(row)
    value["payload"] = json.loads(value.pop("payload_json"))
    return value


def diff_against_baseline(db: Database, task_id: str, scope_version: str) -> SnapshotDiff:
    """Diff current merged assets against the latest persisted baseline.

    The diff is read-only and fully offline. Scope-version mismatch is
    reported, never silently ignored: a baseline from another authorization
    cannot vouch for current results.
    """
    baseline = latest_baseline(db, task_id)
    if baseline is None:
        return SnapshotDiff(scope_version=scope_version, baseline_scope_version=None,
                            scope_version_match=False)
    baseline_scope = baseline["payload"].get("scope_version")
    match = baseline_scope == scope_version
    baseline_assets = {
        (item["asset_type"], item["normalized_value"]): item
        for item in baseline["payload"].get("assets", [])
    }
    current_assets = _current_assets(db, task_id)
    added, disappeared, changed, reappeared = [], [], [], []
    unchanged = 0
    for key, current in current_assets.items():
        previous = baseline_assets.pop(key, None)
        if previous is None:
            added.append({**current, "asset_type": key[0],
                          "normalized_value": key[1]})
            continue
        differences = {field_name for field_name in
                       ("scope_status", "confidence", "architecture_status", "current_status")
                       if previous.get(field_name) != current.get(field_name)}
        if differences:
            changed.append({"asset_type": key[0], "normalized_value": key[1],
                            "fields": sorted(differences),
                            "baseline": {f: previous.get(f) for f in differences},
                            "current": {f: current.get(f) for f in differences}})
        else:
            unchanged += 1
        if current.get("current_status") == "current" and previous.get("current_status") == "disappeared":
            reappeared.append({"asset_type": key[0], "normalized_value": key[1]})
    for key, previous in baseline_assets.items():
        disappeared.append({"asset_type": key[0], "normalized_value": key[1],
                            "scope_status": previous.get("scope_status")})
    return SnapshotDiff(
        scope_version=scope_version,
        baseline_scope_version=baseline_scope,
        scope_version_match=match,
        added=added,
        disappeared=disappeared,
        changed=changed,
        reappeared=reappeared,
        unchanged=unchanged,
    )


def absorb_baseline(db: Database, task_id: str, scope_version: str,
                    *, integrity_status: str) -> dict[str, Any]:
    """Absorb the current state as the new baseline behind the quality gate.

    Absorption requires a matching scope version and an integrity status the
    quality gate accepts (complete or partial) - failed/unknown runs never
    overwrite the monitoring baseline (plan R7).
    """
    gate_passed = integrity_status in {"complete", "partial"}
    if not gate_passed:
        return {"absorbed": False, "reason": f"quality_gate_failed:{integrity_status}"}
    current_scope = next(db.rows(
        "SELECT scope_version FROM task WHERE task_id=?", (task_id,)), None)
    if current_scope is None or current_scope["scope_version"] != scope_version:
        return {"absorbed": False, "reason": "scope_version_mismatch"}
    checkpoint_id = save_baseline(db, task_id, scope_version)
    return {"absorbed": True, "checkpoint_id": checkpoint_id}
