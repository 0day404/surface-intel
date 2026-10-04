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


def _rows(db, query, params=()):
    return [dict(row) for row in db.rows(query, params)]


def test_dns_and_ct_observations_merge_into_assets_and_relations(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A",
        "records": ["203.0.113.5", "198.51.100.7"], "status": "observed",
    })
    _observation(db, "t", "ct_log", {
        "target": "example.com", "status": "observed", "log_source": "crt.sh",
        "certificates": [{
            "common_name": "www.example.com", "sans": ["www.example.com", "example.com"],
            "issuer": "issuer", "serial": "aa", "san_completeness": "full",
        }],
    })
    summary = merge_task_observations(db, "t", scope)
    assert summary.status == "completed"
    assert summary.observations_processed == 2
    assets = {(row["asset_type"], row["normalized_value"]): row
              for row in _rows(db, "SELECT * FROM asset")}
    # domains: anchor www.example.com, anchor example.com, cert SANs
    assert ("domain", "www.example.com") in assets
    assert ("domain", "example.com") in assets
    # ips: authorized one and third-party one
    assert ("ip", "203.0.113.5") in assets
    assert ("ip", "198.51.100.7") in assets
    assert assets[("domain", "www.example.com")]["scope_status"] == "confirmed"
    assert assets[("ip", "203.0.113.5")]["scope_status"] == "confirmed"
    assert assets[("ip", "198.51.100.7")]["scope_status"] == "candidate"
    assert assets[("domain", "www.example.com")]["confidence"] == 1.0  # scope anchor
    assert assets[("ip", "198.51.100.7")]["confidence"] == 0.7  # single strong dns_a
    relations = _rows(db, "SELECT * FROM asset_relation")
    by_type = {}
    for relation in relations:
        by_type.setdefault(relation["relation_type"], []).append(relation)
    assert {row["status"] for row in by_type["dns_a"]} == {"confirmed", "candidate"}
    assert all(row["relation_strength"] == "strong" for row in by_type["cert_name"])
    assert all(row["relation_strength"] == "strong" for row in by_type["dns_a"])
    # provenance links relations back to their observations
    provenance = json.loads(by_type["dns_a"][0]["provenance_json"])
    assert provenance[0]["source"] == "dns_resolver"
    assert provenance[0]["observation_id"]


def test_rdap_merges_org_cidr_asn_as_candidates_with_medium_relations(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "rdap_lookup", {
        "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
        "record": {
            "organizations": ["Hosting Provider Inc"], "registrar": None,
            "cidrs": ["203.0.113/24"], "asn_start": 64512,
        },
        "source_protocol": "rdap",
    })
    merge_task_observations(db, "t", scope)
    assets = {(row["asset_type"], row["normalized_value"]): row
              for row in _rows(db, "SELECT * FROM asset")}
    assert ("ip", "203.0.113.5") in assets
    assert ("organization", "Hosting Provider Inc") in assets
    assert ("cidr", "203.0.113.0/24") in assets  # shorthand padded to full CIDR
    assert ("asn", "64512") in assets
    # attribution classes never confirm, even when the anchor IP is authorized
    for key in (("organization", "Hosting Provider Inc"), ("cidr", "203.0.113.0/24"), ("asn", "64512")):
        assert assets[key]["scope_status"] == "candidate", key
        assert assets[key]["confidence"] <= 0.95
    relations = {row["relation_type"]: row for row in _rows(db, "SELECT * FROM asset_relation")}
    assert relations["rdap_org"]["relation_strength"] == "medium"
    assert relations["rdap_org"]["status"] == "candidate"
    assert relations["rdap_cidr"]["relation_strength"] == "medium"
    assert relations["rdap_asn"]["relation_strength"] == "medium"


def test_not_found_and_infra_observations_create_no_assets(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "rdap_lookup", {
        "target": "203.0.113.5", "target_kind": "ip", "status": "not_found",
    })
    _observation(db, "t", "ct_log", {
        "target": "example.com", "status": "empty", "certificates": [],
    })
    summary = merge_task_observations(db, "t", scope)
    # neither response attaches anything: negatives and infra failures
    # create no relations (and rdap negatives not even an anchor asset)
    relations = _rows(db, "SELECT * FROM asset_relation")
    assert relations == []


def test_conflicting_attribution_values_are_preserved_not_resolved(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "rdap_lookup", {
        "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
        "record": {"organizations": ["Org A Ltd"]}, "source_protocol": "rdap",
    }, observed_at="2026-10-03T10:00:00+00:00")
    _observation(db, "t", "rdap_lookup", {
        "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
        "record": {"organizations": ["Org B Inc"]}, "source_protocol": "rdap",
    }, observed_at="2026-10-03T11:00:00+00:00")
    summary = merge_task_observations(db, "t", scope)
    assert summary.conflicts_created == 1
    conflicts = _rows(db, "SELECT * FROM asset_conflict")
    assert len(conflicts) == 1
    values = json.loads(conflicts[0]["values_json"])
    assert {item["value"] for item in values} == {"Org A Ltd", "Org B Inc"}
    assert conflicts[0]["status"] == "unresolved"
    # both orgs exist as candidate assets - nothing was picked
    assets = {(row["asset_type"], row["normalized_value"]) for row in _rows(db, "SELECT * FROM asset")}
    assert ("organization", "Org A Ltd") in assets
    assert ("organization", "Org B Inc") in assets


def test_same_value_reobserved_is_not_a_conflict(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    for hour in ("10", "11"):
        _observation(db, "t", "rdap_lookup", {
            "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
            "record": {"organizations": ["Org A Ltd"]}, "source_protocol": "rdap",
        }, observed_at=f"2026-10-03T{hour}:00:00+00:00")
    summary = merge_task_observations(db, "t", scope)
    assert summary.conflicts_created == 0


def test_single_observation_multi_valued_org_is_not_a_conflict(tmp_path):
    """One RDAP record listing several contacts is multi-valued, not a
    contradiction (B5): the values must not conflict with each other."""
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "rdap_lookup", {
        "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
        "record": {"organizations": ["Ren Yan Jun", "Liu Xiao Bao"]},
        "source_protocol": "rdap",
    })
    summary = merge_task_observations(db, "t", scope)
    assert summary.conflicts_created == 0
    assets = {(row["asset_type"], row["normalized_value"])
              for row in _rows(db, "SELECT * FROM asset")}
    assert ("organization", "Ren Yan Jun") in assets
    assert ("organization", "Liu Xiao Bao") in assets


def test_identical_multivalued_sets_across_observations_are_not_a_conflict(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    for hour in ("10", "11"):
        _observation(db, "t", "rdap_lookup", {
            "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
            "record": {"organizations": ["Org A Ltd", "Org B Inc"]},
            "source_protocol": "rdap",
        }, observed_at=f"2026-10-03T{hour}:00:00+00:00")
    summary = merge_task_observations(db, "t", scope)
    assert summary.conflicts_created == 0


def test_divergent_value_sets_across_observations_still_conflict(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "rdap_lookup", {
        "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
        "record": {"organizations": ["Org A Ltd"]}, "source_protocol": "rdap",
    }, observed_at="2026-10-03T10:00:00+00:00")
    _observation(db, "t", "rdap_lookup", {
        "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
        "record": {"organizations": ["Org A Ltd", "Org B Inc"]},
        "source_protocol": "rdap",
    }, observed_at="2026-10-03T11:00:00+00:00")
    summary = merge_task_observations(db, "t", scope)
    assert summary.conflicts_created == 1
    conflicts = _rows(db, "SELECT * FROM asset_conflict")
    values = json.loads(conflicts[0]["values_json"])
    # every distinct value stays in the audit trail, nothing was picked
    assert {item["value"] for item in values} == {"Org A Ltd", "Org B Inc"}


def test_remerge_is_idempotent_and_preserves_first_seen(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    }, observed_at="2026-10-03T10:00:00+00:00")
    first = merge_task_observations(db, "t", scope)
    assert first.assets_created == 2 and first.relations_created == 1

    second = merge_task_observations(db, "t", scope)
    assert second.assets_created == 0
    assert second.assets_updated == 2
    assert second.relations_created == 0
    assert second.relations_updated == 1
    assert second.conflicts_created == 0

    asset = next(row for row in _rows(db, "SELECT * FROM asset")
                 if row["normalized_value"] == "203.0.113.5")
    assert asset["first_seen"] == "2026-10-03T10:00:00+00:00"

    # a later observation moves last_seen but never first_seen
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    }, observed_at="2026-10-03T12:00:00+00:00")
    merge_task_observations(db, "t", scope)
    asset = next(row for row in _rows(db, "SELECT * FROM asset")
                 if row["normalized_value"] == "203.0.113.5")
    assert asset["first_seen"] == "2026-10-03T10:00:00+00:00"
    assert asset["last_seen"] == "2026-10-03T12:00:00+00:00"


def test_unknown_sources_are_counted_as_skipped_not_merged(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "fofa", {
        "target": "example.com", "host": "example.com:443", "status": "observed",
    })
    summary = merge_task_observations(db, "t", scope)
    assert summary.observations_processed == 0
    assert summary.observations_skipped == 1


def test_unnormalizable_values_are_reported_not_dropped(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A",
        "records": ["203.0.113.5", "not-an-ip"], "status": "observed",
    })
    summary = merge_task_observations(db, "t", scope)
    assert summary.status == "completed"
    skipped = [item for item in summary.skipped if item["reason"] == "normalization_failed"]
    assert len(skipped) == 1
    assert skipped[0]["value"] == "not-an-ip"


def test_multisource_corroboration_raises_confidence(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.9"],
        "status": "observed",
    }, observed_at="2026-10-03T10:00:00+00:00")
    _observation(db, "t", "ct_log", {
        "target": "www.example.com", "status": "observed", "log_source": "crt.sh",
        "certificates": [{
            "common_name": None, "sans": ["203.0.113.9"],  # unusual but parseable name entry
            "issuer": "i", "serial": "s", "san_completeness": "full",
        }],
    }, observed_at="2026-10-03T11:00:00+00:00")
    merge_task_observations(db, "t", scope)
    asset = next(row for row in _rows(db, "SELECT * FROM asset")
                 if row["normalized_value"] == "203.0.113.9")
    # strong dns_a base 0.7 + one corroboration bonus 0.1 (two sources)
    assert asset["confidence"] == 0.8
    sources = json.loads(asset["sources_json"])
    assert {item["source"] for item in sources} == {"dns_resolver", "ct_log"}
