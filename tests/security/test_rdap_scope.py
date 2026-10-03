import json

import pytest

from surface_intel.policy.scope import Scope
from surface_intel.rdap_runner import run_rdap_lookup
from surface_intel.state.database import Database


class SpyRdapClient:
    def __init__(self, record):
        self.record = record
        self.calls = []

    async def lookup(self, target, kind):
        self.calls.append((target, kind))
        return {"status": "observed", "record": self.record, "source_protocol": "rdap"}


def _record(**overrides):
    row = {
        "handle": "NET-X",
        "name": "EXAMPLE-NET",
        "organizations": ["Example Org Ltd"],
        "registrar": "Example Registrar Co.",
        "abuse_contacts": ["abuse@registrar.example"],
        "events": {"registration": "2020-01-01T00:00:00", "expiration": None, "last_changed": None},
        "cidrs": ["203.0.113/24"],
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


def _payload(db):
    rows = list(db.rows("SELECT payload_json FROM observation ORDER BY observation_id"))
    return [json.loads(row["payload_json"]) for row in rows]


@pytest.mark.asyncio
async def test_unconfirmed_scope_blocks_before_client_call(tmp_path):
    scope = _scope(confirmed=False)
    db = _setup(tmp_path, scope)
    client = SpyRdapClient(_record())
    result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="example.com", kind="domain")
    assert result.status == "blocked"
    assert result.error == "scope_not_confirmed"
    assert client.calls == []
    assert list(db.rows("SELECT 1 FROM observation")) == []


@pytest.mark.asyncio
async def test_out_of_scope_domain_and_ip_block_before_client_call(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = SpyRdapClient(_record())
    domain_result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="other.net", kind="domain")
    ip_result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="198.51.100.9", kind="ip")
    assert domain_result.error == "target_out_of_scope"
    assert ip_result.error == "target_out_of_scope"
    assert client.calls == []


@pytest.mark.asyncio
async def test_unbound_asn_blocked_until_scope_or_binding_exists(tmp_path):
    """Neither an authorized_asns entry nor a merge binding: refused pre-call."""
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = SpyRdapClient(_record())
    result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="64512", kind="asn")
    assert result.status == "blocked"
    assert result.error == "asn_binding_not_found"
    assert client.calls == []
    assert list(db.rows("SELECT 1 FROM observation")) == []


@pytest.mark.asyncio
async def test_user_authorized_asn_passes_via_scope(tmp_path):
    authorized = Scope(
        authorized_domains=("example.com", "*.example.com"),
        authorized_ips=("203.0.113.5",),
        authorized_asns=("AS64512",),
        confirmed=True,
        scope_version="scope-v1",
    )
    db = _setup(tmp_path, authorized)
    client = SpyRdapClient(_record(asn_start=64512, asn_end=64512))
    result = await run_rdap_lookup(db, client, task_id="t", scope=authorized, target="64512", kind="asn")
    assert result.status == "completed"
    assert client.calls == [("64512", "asn")]


@pytest.mark.asyncio
async def test_ip_in_authorized_cidr_is_accepted(tmp_path):
    scope = _scope(authorized_ips=(), authorized_cidrs=("203.0.113.0/24",))
    db = _setup(tmp_path, scope)
    client = SpyRdapClient(_record())
    result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="203.0.113.77", kind="ip")
    assert result.status == "completed"
    assert client.calls == [("203.0.113.77", "ip")]


@pytest.mark.asyncio
async def test_organization_and_asn_match_never_confirm_attribution(tmp_path):
    """Registration data naming an authorized target's org stays candidate-only.

    Even when the record's organization matches a name the analyst believes
    owns the target, RDAP observations never produce `confirmed` relations.
    """
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = SpyRdapClient(_record(organizations=["Example Org Ltd"], asn_start=64512, asn_end=64512))
    result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="203.0.113.5", kind="ip")
    assert result.status == "completed"
    for payload in _payload(db):
        assert payload["related_scope_status"] == "candidate"
        assert payload["related_scope_status"] != "confirmed"


@pytest.mark.asyncio
async def test_wider_rdap_cidr_stays_candidate_not_scope_expansion(tmp_path):
    """The record's /24 around an authorized /32 is a candidate relation, not scope."""
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = SpyRdapClient(_record(cidrs=["203.0.113/24"]))
    await run_rdap_lookup(db, client, task_id="t", scope=scope, target="203.0.113.5", kind="ip")
    payload = _payload(db)[0]
    assert payload["related_scope_status"] == "candidate"
    # the surrounding /24 must not silently become authorized: a follow-up
    # lookup of a neighbor IP is still refused
    client2 = SpyRdapClient(_record())
    neighbor = await run_rdap_lookup(db, client2, task_id="t", scope=scope, target="203.0.113.6", kind="ip")
    assert neighbor.status == "blocked"
    assert neighbor.error == "target_out_of_scope"


@pytest.mark.asyncio
async def test_rdap_observations_never_create_assets(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = SpyRdapClient(_record())
    await run_rdap_lookup(db, client, task_id="t", scope=scope, target="203.0.113.5", kind="ip")
    assert next(db.rows("SELECT COUNT(*) AS count FROM asset"))["count"] == 0
