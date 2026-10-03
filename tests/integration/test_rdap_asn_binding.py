"""R4.3: ASN lookups unlocked by merge bindings.

The R3.3 promise: once the merge layer provides an attribution basis
(an `rdap_asn` relation from a scope-confirmed asset), an ASN lookup is
allowed. The action's scope-checked target stays the in-scope anchor while
the ASN rides `lookup_target`, fully visible in the audit trail.
"""

import json

import pytest

from surface_intel.merger import merge_task_observations
from surface_intel.policy.scope import Scope
from surface_intel.rdap_runner import run_rdap_lookup
from surface_intel.state.database import Database


class StubRdapClient:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    async def lookup(self, target, kind):
        self.calls.append((target, kind))
        return dict(self.outcomes.get((target, kind), {"status": "not_found", "source_protocol": "rdap"}))


def _record(**overrides):
    row = {
        "handle": "NET-203-0-113-0-1",
        "name": "EXAMPLE-NET",
        "organizations": ["Hosting Provider Inc"],
        "cidrs": ["203.0.113/24"],
        "asn_start": 64512,
        "asn_end": 64512,
    }
    row.update(overrides)
    return row


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


def _payload(db, query, params=()):
    return [dict(row) for row in db.rows(query, params)]


@pytest.mark.asyncio
async def test_merge_binding_unlocks_asn_lookup_end_to_end(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = StubRdapClient({
        ("203.0.113.5", "ip"): {"status": "observed", "record": _record(), "source_protocol": "rdap"},
        ("64512", "asn"): {"status": "observed", "record": {
            "handle": "AS-64512", "name": "HOSTING-AS", "asn_start": 64512, "asn_end": 64512,
        }, "source_protocol": "rdap"},
    })

    # before any merge: no binding, ASN lookup refused
    blocked = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="64512", kind="asn")
    assert blocked.status == "blocked"
    assert blocked.error == "asn_binding_not_found"

    # authorized IP lookup surfaces ASN 64512; merge binds it to the confirmed IP
    first = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="203.0.113.5", kind="ip")
    assert first.status == "completed"
    merge_task_observations(db, "t", scope)
    relations = _payload(db, """SELECT r.relation_type, src.scope_status AS src_scope
                                FROM asset_relation r
                                JOIN asset src ON src.asset_id=r.from_asset_id
                                JOIN asset dst ON dst.asset_id=r.to_asset_id
                                WHERE dst.asset_type='asn'""")
    assert any(r["relation_type"] == "rdap_asn" and r["src_scope"] == "confirmed" for r in relations)

    # now the ASN lookup passes the gate and queries the ASN itself
    result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="64512", kind="asn")
    assert result.status == "completed"
    assert ("64512", "asn") in client.calls
    observations = _payload(db, "SELECT payload_json FROM observation ORDER BY rowid")
    asn_observation = json.loads(observations[-1]["payload_json"])
    assert asn_observation["target"] == "64512"
    assert asn_observation["target_kind"] == "asn"
    assert asn_observation["scope_anchor"] == "203.0.113.5"
    # attribution data still stays candidate-only
    assert asn_observation["related_scope_status"] == "candidate"


@pytest.mark.asyncio
async def test_binding_from_unconfirmed_source_is_not_a_basis(tmp_path):
    """A relation whose anchor is out of scope never justifies an ASN lookup."""
    scope = _scope(authorized_ips=())  # the IP itself is not authorized
    db = _setup(tmp_path, scope)
    client = StubRdapClient({
        ("203.0.113.5", "ip"): {"status": "observed", "record": _record(), "source_protocol": "rdap"},
    })
    # the IP lookup is out of scope; craft the observations directly instead
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO observation(observation_id,task_id,source,target,payload_json)
               VALUES('obs-1','t','rdap_lookup','203.0.113.5',?)""",
            (json.dumps({
                "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
                "record": _record(), "source_protocol": "rdap",
                "observed_at": "2026-10-03T10:00:00+00:00",
            }),),
        )
    merge_task_observations(db, "t", scope)
    relations = _payload(db, "SELECT * FROM asset_relation WHERE relation_type='rdap_asn'")
    assert relations  # the relation exists...
    result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="64512", kind="asn")
    assert result.status == "blocked"
    assert result.error == "asn_binding_not_found"  # ...but its anchor is not confirmed
    assert client.calls == []


@pytest.mark.asyncio
async def test_binding_action_audits_anchor_and_lookup_target(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = StubRdapClient({
        ("203.0.113.5", "ip"): {"status": "observed", "record": _record(), "source_protocol": "rdap"},
        ("64512", "asn"): {"status": "observed", "record": {"asn_start": 64512}, "source_protocol": "rdap"},
    })
    await run_rdap_lookup(db, client, task_id="t", scope=scope, target="203.0.113.5", kind="ip")
    merge_task_observations(db, "t", scope)
    await run_rdap_lookup(db, client, task_id="t", scope=scope, target="64512", kind="asn")
    calls = _payload(db, "SELECT input_summary FROM adapter_call ORDER BY rowid")
    summary = json.loads(calls[-1]["input_summary"])
    assert summary["target"] == "203.0.113.5"  # in-scope anchor passed the executor
    assert summary["lookup_target"] == "64512"
    assert summary["scope_basis"].startswith("merge_binding:")
