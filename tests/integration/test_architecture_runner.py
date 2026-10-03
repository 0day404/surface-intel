import json

import pytest

from surface_intel.architecture import annotate_architecture
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


def _asset(db, asset_type, value):
    row = next(db.rows(
        "SELECT * FROM asset WHERE task_id='t' AND asset_type=? AND normalized_value=?",
        (asset_type, value),
    ), None)
    return dict(row) if row else None


def test_cdn_and_waf_signals_classify_likely_with_evidence(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "CNAME",
        "records": ["www.example.com.cdn.cloudflare.net"], "status": "observed",
    })
    _observation(db, "t", "ct_log", {
        "target": "www.example.com", "status": "observed", "log_source": "crt.sh",
        "certificates": [{
            "common_name": "www.example.com", "sans": ["www.example.com"],
            "issuer": "Cloudflare Inc ECC CA-3", "serial": "aa", "san_completeness": "full",
        }],
    })
    merge_task_observations(db, "t", scope)
    summary = annotate_architecture(db, "t")
    asset = _asset(db, "domain", "www.example.com")
    assert asset["architecture_status"] == "cdn_likely"  # cname + issuer corroborate cdn
    evidence = json.loads(asset["architecture_json"])
    assert len(evidence) >= 2
    assert {item["signal"] for item in evidence} == {"dns_cname", "cert_issuer"}
    assert all(item["observation_id"] for item in evidence)  # every signal traceable
    assert summary.assets_classified >= 1


def test_managed_dns_only_yields_dns_candidate(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "app.example.com", "record_type": "NS",
        "records": ["ns1.ns.cloudflare.com"], "status": "observed",
    })
    merge_task_observations(db, "t", scope)
    annotate_architecture(db, "t")
    asset = _asset(db, "domain", "app.example.com")
    assert asset["architecture_status"] == "dns_candidate"


def test_cloud_org_from_rdap_classifies_ip_hosting(tmp_path):
    scope = _scope(authorized_ips=("203.0.113.5",))
    db = _setup(tmp_path, scope)
    _observation(db, "t", "rdap_lookup", {
        "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
        "record": {"organizations": ["Hetzner Online GmbH"]},
    })
    merge_task_observations(db, "t", scope)
    annotate_architecture(db, "t")
    asset = _asset(db, "ip", "203.0.113.5")
    assert asset["architecture_status"] == "cloud_hosting_candidate"


def test_out_of_scope_related_asset_marked_third_party(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "CNAME",
        "records": ["cdn.partner-hosting.net"], "status": "observed",
    })
    merge_task_observations(db, "t", scope)
    summary = annotate_architecture(db, "t")
    asset = _asset(db, "domain", "cdn.partner-hosting.net")
    assert asset["architecture_status"] == "third_party"
    assert asset["scope_status"] == "candidate"  # third_party never touches scope
    assert summary.assets_third_party >= 1


def test_unrelated_assets_stay_unknown(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "plain.example.com", "record_type": "A", "records": ["203.0.113.5"],
        "status": "observed",
    })
    merge_task_observations(db, "t", scope)
    summary = annotate_architecture(db, "t")
    asset = _asset(db, "domain", "plain.example.com")
    assert asset["architecture_status"] == "unknown"
    assert summary.assets_unchanged >= 1


def test_annotation_is_idempotent(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "CNAME",
        "records": ["www.example.com.cdn.cloudflare.net"], "status": "observed",
    })
    merge_task_observations(db, "t", scope)
    first = annotate_architecture(db, "t")
    asset = _asset(db, "domain", "www.example.com")
    evidence_first = json.loads(asset["architecture_json"])
    second = annotate_architecture(db, "t")
    asset = _asset(db, "domain", "www.example.com")
    evidence_second = json.loads(asset["architecture_json"])
    assert first.status == second.status == "completed"
    # re-annotation rewrites deterministically - evidence never accumulates
    assert evidence_first == evidence_second
    # one CNAME observation matching a cdn+waf vendor yields two entries
    assert len(evidence_first) == 2
    assert asset["architecture_status"] == "cdn_candidate"
