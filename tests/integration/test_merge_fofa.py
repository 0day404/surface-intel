import json

import pytest

from surface_intel.merger import merge_task_observations, normalize_asset_value
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
             task_id, source, str(payload.get("host") or payload.get("target") or payload.get("ip") or ""),
             json.dumps(payload, ensure_ascii=True, sort_keys=True)),
        )


def _rows(db, query, params=()):
    return [dict(row) for row in db.rows(query, params)]


@pytest.mark.parametrize("raw,expected", [
    ("203.0.113.5:443/https", "203.0.113.5:443/https"),
    ("203.0.113.5:443/HTTPS", "203.0.113.5:443/https"),
    ("203.0.113.5:8080/", "203.0.113.5:8080/unknown"),
    ("not-an-ip:443/https", None),
    ("203.0.113.5:port/https", None),
])
def test_service_normalization(raw, expected):
    assert normalize_asset_value("service", raw) == expected


def test_fofa_result_merges_host_ip_service(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "fofa_mcp", {
        "host": "www.example.com", "ip": "203.0.113.5", "port": 443,
        "protocol": "https", "title": "Example", "status": "observed",
    })
    summary = merge_task_observations(db, "t", scope)
    assert summary.observations_processed == 1  # fofa no longer counted as skipped
    assets = {(row["asset_type"], row["normalized_value"]): row
              for row in _rows(db, "SELECT * FROM asset")}
    assert ("domain", "www.example.com") in assets
    assert ("ip", "203.0.113.5") in assets
    service = assets[("service", "203.0.113.5:443/https")]
    assert service["port"] == 443
    assert service["protocol"] == "https"
    assert service["scope_status"] == "confirmed"  # service on an authorized address
    assert service["confidence"] <= 0.95           # ...but evidence confidence is still bounded
    relations = {row["relation_type"]: row for row in _rows(db, "SELECT * FROM asset_relation")}
    assert relations["fofa_host_ip"]["relation_strength"] == "strong"
    assert relations["service_ip"]["relation_strength"] == "strong"


def test_fofa_third_party_service_stays_candidate(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "fofa_mcp", {
        "host": "www.example.com", "ip": "198.51.100.7", "port": 8080,
        "protocol": "http", "status": "observed",
    })
    merge_task_observations(db, "t", scope)
    assets = {(row["asset_type"], row["normalized_value"]): row
              for row in _rows(db, "SELECT * FROM asset")}
    assert assets[("service", "198.51.100.7:8080/http")]["scope_status"] == "candidate"
    assert assets[("ip", "198.51.100.7")]["scope_status"] == "candidate"


def test_fofa_domain_field_creates_relation_when_distinct(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "fofa_mcp", {
        "host": "www.example.com", "domain": "mail.example.com", "ip": "203.0.113.5",
        "port": 25, "protocol": "smtp", "status": "observed",
    })
    merge_task_observations(db, "t", scope)
    relations = _rows(db, "SELECT * FROM asset_relation WHERE relation_type='fofa_domain_ip'")
    assert len(relations) == 1
    assets = {(row["asset_type"], row["normalized_value"]) for row in _rows(db, "SELECT * FROM asset")}
    assert ("domain", "mail.example.com") in assets
    assert ("service", "203.0.113.5:25/smtp") in assets


def test_cross_source_corroboration_through_fofa(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    })
    _observation(db, "t", "fofa_mcp", {
        "host": "www.example.com", "ip": "203.0.113.5", "port": 443,
        "protocol": "https", "status": "observed",
    })
    merge_task_observations(db, "t", scope)
    ip_asset = next(row for row in _rows(db, "SELECT * FROM asset")
                    if row["normalized_value"] == "203.0.113.5")
    # authorized observation confirmed by a second source: 0.7 base + 0.1 bonus
    assert ip_asset["confidence"] == 0.8
    sources = {item["source"] for item in json.loads(ip_asset["sources_json"])}
    assert sources == {"dns_resolver", "fofa_mcp"}
