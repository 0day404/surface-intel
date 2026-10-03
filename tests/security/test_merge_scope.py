"""Merge-layer security invariants.

The merge layer may record and relate anything it observes, but it must
never upgrade authorization: weak similarity is never confirmed, attribution
classes never confirm, and out-of-scope neighbors stay out of scope no
matter how many sources agree.
"""

import json

import pytest

from surface_intel.merger import merge_task_observations
from surface_intel.policy.scope import Scope
from surface_intel.state.database import Database


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
        authorized_ips=("203.0.113.5",),
        confirmed=True,
        scope_version="scope-v1",
    )
    defaults.update(overrides)
    return Scope(**defaults)


def _observation(db, task_id, source, payload, observed_at="2026-10-03T10:00:00+00:00"):
    payload = dict(payload)
    payload.setdefault("observed_at", observed_at)
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO observation(observation_id,task_id,source,target,payload_json)
               VALUES(?,?,?,?,?)""",
            (f"obs-{source}-{abs(hash(json.dumps(payload, sort_keys=True))) % 10**12}",
             task_id, source, str(payload.get("target", "")),
             json.dumps(payload, ensure_ascii=True, sort_keys=True)),
        )


def _assets(db):
    return {(row["asset_type"], row["normalized_value"]): row
            for row in db.rows("SELECT * FROM asset")}


def _relations(db):
    return [dict(row) for row in db.rows("SELECT * FROM asset_relation")]


def test_wide_rdap_cidr_and_neighbor_ip_stay_out_of_scope(tmp_path):
    """The registry /24 around an authorized IP never widens authorization."""
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "rdap_lookup", {
        "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
        "record": {"organizations": ["Hosting Provider Inc"], "cidrs": ["203.0.113/24"]},
    })
    merge_task_observations(db, "t", scope)
    assets = _assets(db)
    assert assets[("cidr", "203.0.113.0/24")]["scope_status"] == "candidate"
    # the adjacent address is not authorized even though the network says /24
    assert scope.is_in_scope("203.0.113.6") is False


def test_org_match_and_multi_source_agreement_never_confirm_third_party(tmp_path):
    """Three sources naming the same third-party host still leave it candidate."""
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "CNAME",
        "records": ["cdn.partner-hosting.net"], "status": "observed",
    })
    _observation(db, "t", "ct_log", {
        "target": "www.example.com", "status": "observed", "log_source": "crt.sh",
        "certificates": [{"common_name": "cdn.partner-hosting.net",
                          "sans": ["cdn.partner-hosting.net"], "issuer": "i",
                          "serial": "s", "san_completeness": "full"}],
    })
    _observation(db, "t", "rdap_lookup", {
        "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
        "record": {"organizations": ["Partner Hosting Inc"]},
    })
    merge_task_observations(db, "t", scope)
    assets = _assets(db)
    third_party = assets[("domain", "cdn.partner-hosting.net")]
    assert third_party["scope_status"] == "candidate"
    assert third_party["confidence"] <= 0.95
    org = assets[("organization", "Partner Hosting Inc")]
    assert org["scope_status"] == "candidate"


def test_no_weak_relations_are_ever_created_or_confirmed(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    })
    merge_task_observations(db, "t", scope)
    relations = _relations(db)
    assert relations, "expected structural relations from DNS"
    assert all(row["relation_strength"] in {"strong", "medium"} for row in relations)
    weak_confirmed = [row for row in relations
                      if row["relation_strength"] == "weak" and row["status"] == "confirmed"]
    assert weak_confirmed == []


def test_only_strong_relations_to_confirmed_assets_are_confirmed(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    })
    _observation(db, "t", "rdap_lookup", {
        "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
        "record": {"organizations": ["Hosting Provider Inc"]},
    })
    merge_task_observations(db, "t", scope)
    by_type = {row["relation_type"]: row for row in _relations(db)}
    # strong DNS relation between two confirmed assets is confirmed...
    assert by_type["dns_a"]["status"] == "confirmed"
    # ...while the medium attribution relation stays candidate even though
    # both endpoints are confirmed/candidate assets
    assert by_type["rdap_org"]["status"] == "candidate"
    assert by_type["rdap_org"]["relation_strength"] == "medium"


def test_conflicts_are_never_resolved_by_the_merger(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    for org in ("Org A Ltd", "Org B Inc"):
        _observation(db, "t", "rdap_lookup", {
            "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
            "record": {"organizations": [org]},
        })
    merge_task_observations(db, "t", scope)
    conflicts = [dict(row) for row in db.rows("SELECT * FROM asset_conflict")]
    assert len(conflicts) == 1
    assert conflicts[0]["status"] == "unresolved"
    # no observed value was discarded: both remain as assets and in the conflict
    values = {item["value"] for item in json.loads(conflicts[0]["values_json"])}
    assert values == {"Org A Ltd", "Org B Inc"}
    assets = _assets(db)
    assert ("organization", "Org A Ltd") in assets
    assert ("organization", "Org B Inc") in assets


def test_historical_layering_keeps_all_observed_values(tmp_path):
    """A changing DNS answer yields two IP assets, not an overwrite."""
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    }, observed_at="2026-10-03T10:00:00+00:00")
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["198.51.100.7"],
        "status": "observed",
    }, observed_at="2026-10-03T11:00:00+00:00")
    summary = merge_task_observations(db, "t", scope)
    assets = _assets(db)
    assert ("ip", "203.0.113.5") in assets
    assert ("ip", "198.51.100.7") in assets
    # the changed answer is a historical transition, not an attribution conflict
    assert summary.conflicts_created == 0
