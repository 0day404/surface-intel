"""R4.6: evidence-age decay and relation lifecycle classification."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from surface_intel.merger import (
    asset_confidence,
    freshness_decay,
    merge_task_observations,
)
from surface_intel.policy.scope import Scope
from surface_intel.state.database import Database


NOW = "2026-10-03T12:00:00+00:00"


def _as_iso(days_ago):
    stamp = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc) - timedelta(days=days_ago)
    return stamp.isoformat()


def _setup(tmp_path, scope):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0',?)",
            (scope.scope_version,),
        )
    return db


def _scope(**overrides):
    defaults = dict(
        authorized_domains=("example.com", "*.example.com"),
        confirmed=True,
        scope_version="scope-v1",
    )
    defaults.update(overrides)
    return Scope(**defaults)


def _observation(db, task_id, source, payload, observed_at):
    payload = dict(payload)
    payload["observed_at"] = observed_at
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO observation(observation_id,task_id,source,target,payload_json)
               VALUES(?,?,?,?,?)""",
            (f"obs-{source}-{abs(hash(json.dumps(payload, sort_keys=True))) % 10**12}",
             task_id, source, str(payload.get("target", "")),
             json.dumps(payload, ensure_ascii=True, sort_keys=True)),
        )


def _rows(db, query, params=()):
    return [dict(row) for row in db.rows(query, params)]


def test_freshness_decay_tiers_are_deterministic():
    assert freshness_decay(None, NOW) == 0.0
    assert freshness_decay(_as_iso(0), NOW) == 0.0
    assert freshness_decay(_as_iso(7), NOW) == 0.0
    assert freshness_decay(_as_iso(8), NOW) == -0.1
    assert freshness_decay(_as_iso(30), NOW) == -0.1
    assert freshness_decay(_as_iso(31), NOW) == -0.2
    assert freshness_decay("not-a-date", NOW) == 0.0


def test_decay_lowers_stale_evidence_but_scope_anchors_never_decay():
    fresh = asset_confidence(scope_status="candidate", scope_anchored=False,
                             strengths={"strong"}, source_count=1,
                             last_seen=_as_iso(0), as_of=NOW)
    stale = asset_confidence(scope_status="candidate", scope_anchored=False,
                             strengths={"strong"}, source_count=1,
                             last_seen=_as_iso(40), as_of=NOW)
    assert fresh == 0.7
    assert stale == 0.5  # 0.7 - 0.2 staleness
    assert asset_confidence(scope_status="candidate", scope_anchored=False,
                            strengths={"weak"}, source_count=1,
                            last_seen=_as_iso(365), as_of=NOW) == 0.2  # floor
    # user anchors are authorization, not aging evidence
    assert asset_confidence(scope_status="confirmed", scope_anchored=True,
                            strengths=set(), source_count=1,
                            last_seen=_as_iso(365), as_of=NOW) == 1.0


def test_relation_lifecycle_new_current_disappeared_reappeared(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    day1, day2, day3 = _as_iso(3), _as_iso(2), _as_iso(1)

    # run 1: authorized anchor resolves to 203.0.113.5 -> relation is new/current
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    }, observed_at=day1)
    merge_task_observations(db, "t", scope)
    relation = next(r for r in _rows(db, "SELECT * FROM asset_relation")
                    if r["relation_type"] == "dns_a")
    assert relation["current_status"] == "current"
    assert relation["last_transition"] == "new"

    # run 2: the answer changed -> old value disappeared, new value current
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["198.51.100.7"],
        "status": "observed",
    }, observed_at=day2)
    merge_task_observations(db, "t", scope)
    by_value = {r["normalized_value"]: r for r in _rows(
        db, """SELECT a.normalized_value, r.current_status, r.last_transition
               FROM asset_relation r JOIN asset a ON a.asset_id=r.to_asset_id
               WHERE r.relation_type='dns_a'""")}
    assert by_value["203.0.113.5"]["current_status"] == "disappeared"
    assert by_value["203.0.113.5"]["last_transition"] == "disappeared"
    assert by_value["198.51.100.7"]["current_status"] == "current"

    # run 3: the original answer returns -> reappeared (临时变化后重现)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    }, observed_at=day3)
    merge_task_observations(db, "t", scope)
    by_value = {r["normalized_value"]: r for r in _rows(
        db, """SELECT a.normalized_value, r.current_status, r.last_transition
               FROM asset_relation r JOIN asset a ON a.asset_id=r.to_asset_id
               WHERE r.relation_type='dns_a'""")}
    assert by_value["203.0.113.5"]["current_status"] == "current"
    assert by_value["203.0.113.5"]["last_transition"] == "reappeared"
    assert by_value["198.51.100.7"]["current_status"] == "disappeared"

    # no relation ever confirmed itself through lifecycle movement
    assert all(r["status"] != "confirmed" or r["relation_strength"] == "strong"
               for r in _rows(db, "SELECT * FROM asset_relation"))


def test_disappeared_asset_rollup_and_stable_remerge(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    }, observed_at=_as_iso(3))
    merge_task_observations(db, "t", scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["198.51.100.7"],
        "status": "observed",
    }, observed_at=_as_iso(1))
    merge_task_observations(db, "t", scope)
    assets = {row["normalized_value"]: row for row in _rows(db, "SELECT * FROM asset")}
    # the anchor stays current; the displaced third-party value disappears
    assert assets["www.example.com"]["current_status"] == "current"
    assert assets["203.0.113.5"]["current_status"] == "disappeared"
    assert assets["198.51.100.7"]["current_status"] == "current"
    # authorized assets never disappear
    assert assets["203.0.113.5"]["scope_status"] == "candidate"  # out-of-scope value
    authorized_ip = Scope(authorized_domains=("example.com", "*.example.com"),
                          authorized_ips=("203.0.113.5",), confirmed=True,
                          scope_version="scope-v1")
    db2 = _setup(tmp_path / "second", authorized_ip)
    _observation(db2, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    }, observed_at=_as_iso(3))
    _observation(db2, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["198.51.100.7"],
        "status": "observed",
    }, observed_at=_as_iso(1))
    merge_task_observations(db2, "t", authorized_ip)
    # lifecycle never touches scope authorization
    assets2 = {row["normalized_value"]: row for row in _rows(db2, "SELECT * FROM asset")}
    assert assets2["203.0.113.5"]["scope_status"] == "confirmed"


@pytest.mark.asyncio
async def test_remerge_preserves_last_transition_history(tmp_path):
    """Idempotent re-merges must not erase the recorded lifecycle transition."""
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    }, observed_at="2026-10-04T10:00:00+00:00")
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    }, observed_at="2026-10-04T11:00:00+00:00")
    merge_task_observations(db, "t", scope)
    relation = next(r for r in _rows(db, "SELECT * FROM asset_relation")
                    if r["relation_type"] == "dns_a")
    assert relation["last_transition"] == "new"
    merge_task_observations(db, "t", scope)
    relation = next(r for r in _rows(db, "SELECT * FROM asset_relation")
                    if r["relation_type"] == "dns_a")
    assert relation["current_status"] == "current"
    assert relation["last_transition"] == "new"  # preserved, not overwritten
