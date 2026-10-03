"""Architecture-layer security invariants.

Passive architecture identification may only ever produce candidate/likely
levels and third_party markers. The plan's confirmed_direct /
confirmed_hosted / origin_candidate statuses belong to the active phase with
human gates - the passive annotator must never write them, and
classification must never touch scope authorization.
"""

import json

import pytest

from surface_intel.architecture import (
    ARCHITECTURE_STATUSES,
    annotate_architecture,
)
from surface_intel.merger import merge_task_observations
from surface_intel.policy.scope import Scope
from surface_intel.state.database import Database


CONFIRMED_FORBIDDEN = {"confirmed_direct", "confirmed_hosted", "origin_candidate"}


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


def _assets(db):
    return [dict(row) for row in db.rows("SELECT * FROM asset")]


def test_heavy_vendor_signals_still_never_write_confirmed_statuses(tmp_path):
    """Even unanimous CDN evidence stays at likely - probing gates are R6."""
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "CNAME",
        "records": ["www.example.com.cdn.cloudflare.net"], "status": "observed",
    })
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "NS",
        "records": ["ns1.ns.cloudflare.com"], "status": "observed",
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
    assert summary.status == "completed"
    for asset in _assets(db):
        assert asset["architecture_status"] in ARCHITECTURE_STATUSES
        assert asset["architecture_status"] not in CONFIRMED_FORBIDDEN
    classified = next(a for a in _assets(db) if a["normalized_value"] == "www.example.com")
    assert classified["architecture_status"] == "cdn_likely"


def test_classification_never_changes_scope_status(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "CNAME",
        "records": ["x.cdn.cloudflare.net"], "status": "observed",
    })
    merge_task_observations(db, "t", scope)
    scope_before = {row["normalized_value"]: row["scope_status"]
                    for row in db.rows("SELECT * FROM asset")}
    annotate_architecture(db, "t")
    scope_after = {row["normalized_value"]: row["scope_status"]
                   for row in db.rows("SELECT * FROM asset")}
    assert scope_before == scope_after


def test_third_party_assets_never_become_scope_confirmed(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "CNAME",
        "records": ["cdn.partner-hosting.net"], "status": "observed",
    })
    _observation(db, "t", "ct_log", {
        "target": "cdn.partner-hosting.net", "status": "observed", "log_source": "crt.sh",
        "certificates": [{
            "common_name": "cdn.partner-hosting.net", "sans": ["cdn.partner-hosting.net"],
            "issuer": "Let's Encrypt", "serial": "bb", "san_completeness": "full",
        }],
    })
    merge_task_observations(db, "t", scope)
    annotate_architecture(db, "t")
    partner = next(a for a in _assets(db) if a["normalized_value"] == "cdn.partner-hosting.net")
    assert partner["scope_status"] == "candidate"
    assert partner["architecture_status"] in {"third_party", "unknown"}


def test_architecture_evidence_always_carries_observation_refs(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    _observation(db, "t", "dns_resolver", {
        "target": "www.example.com", "record_type": "CNAME",
        "records": ["www.example.com.cdn.cloudflare.net"], "status": "observed",
    })
    merge_task_observations(db, "t", scope)
    annotate_architecture(db, "t")
    asset = next(a for a in _assets(db) if a["normalized_value"] == "www.example.com")
    evidence = json.loads(asset["architecture_json"])
    assert evidence, "classified assets must carry evidence"
    for item in evidence:
        assert item.get("observation_id")
        assert item.get("source")
