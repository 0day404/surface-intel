"""P1-5: tested full-recon orchestration (promoted from the ad-hoc driver).

Stubs for all five sources verify the discovery chain end to end: FOFA rows
seed hosts/IPs, CT SANs extend the host set, DNS A records extend the IP
set, RDAP runs on the capped IP set, and the merge/architecture layers see
everything collected."""
import json

import pytest

from surface_intel.orchestration.full_recon import run_full_passive_recon
from surface_intel.policy.scope import ActiveLevel, Scope
from surface_intel.state.database import Database


class StubFofa:
    def __init__(self, rows):
        self.rows = rows

    async def call_tool(self, name, args):
        assert name in {"fofa_search", "fofa_search_next"}
        return {"status": "completed", "results": self.rows,
                "has_more": False, "request_id": "r", "cost_units": 0}


class StubCt:
    def __init__(self, certs):
        self.certs = certs
        self.calls = []

    async def search(self, domain):
        self.calls.append(domain)
        return {"status": "observed", "certificates": self.certs,
                "log_source": "ctlogs.dev", "queried_at": "t0", "query_ms": 1}


class StubDns:
    def __init__(self, answers):
        self.answers = answers

    async def resolve(self, name, record_type):
        if record_type == "A":
            return {"status": "observed", "records": self.answers.get(name, []),
                    "ttl": 300}
        return {"status": "empty", "records": []}


class StubRdap:
    def __init__(self):
        self.calls = []

    async def lookup(self, target, kind):
        self.calls.append((target, kind))
        return {"status": "observed", "source_protocol": "rdap",
                "record": {"organizations": ["Example Hosting"], "cidrs": []},
                "queried_at": "t0", "query_ms": 1}


def _scope(**overrides):
    defaults = dict(authorized_domains=("example.com", "*.example.com"),
                    confirmed=True, scope_version="scope-v1",
                    active_testing_allowed=False,
                    active_testing_level=ActiveLevel.PASSIVE_ONLY)
    defaults.update(overrides)
    return Scope(**defaults)


def _setup(tmp_path, scope):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0',?)",
            (scope.scope_version,))
    return db


@pytest.mark.asyncio
async def test_full_recon_discovery_chain_end_to_end(tmp_path):
    # IPs pre-authorized, mirroring the real two-pass flow (recon discovers
    # addresses, scope-amend authorizes them, second pass runs RDAP)
    scope = _scope(authorized_ips=("203.0.113.5", "203.0.113.9"))
    db = _setup(tmp_path, scope)
    fofa = StubFofa([{"host": "www.example.com", "ip": "203.0.113.5",
                      "port": 443, "protocol": "https"}])
    ct = StubCt([{"common_name": "app.example.com", "sans": ["app.example.com"],
                  "issuer": "i", "serial": "s", "not_before": "n", "not_after": "x",
                  "first_seen": "f", "last_seen": "l", "log_id": 1}])
    dns = StubDns({"www.example.com": ["203.0.113.5"],
                   "app.example.com": ["203.0.113.9"]})
    rdap = StubRdap()

    result = await run_full_passive_recon(
        db, task_id="t", scope=scope,
        fofa_client=fofa, ct_client=ct, dns_client=dns, rdap_client=rdap,
        fofa_query='domain="example.com"', ct_domain="example.com",
        max_rdap_ips=5,
    )
    assert result.status == "completed"
    # discovery chain: FOFA host + CT SANs both resolved
    assert set(result.discovered_hosts) == {"www.example.com", "app.example.com",
                                            "example.com"}
    # DNS A records feed the IP set; capped RDAP covers discovered addresses
    assert set(result.discovered_ips) == {"203.0.113.5", "203.0.113.9"}
    assert set(result.rdap) == {"203.0.113.5", "203.0.113.9"}
    assert result.rdap["203.0.113.5"]["status"] == "completed"
    # merge + architecture ran over everything
    assert result.merge["observations_processed"] >= 4
    assets = {(row["asset_type"], row["normalized_value"]) for row in
              db.rows("SELECT asset_type,normalized_value FROM asset WHERE task_id='t'")}
    assert ("domain", "app.example.com") in assets  # CT SAN became an asset
    assert ("ip", "203.0.113.9") in assets
    assert ("service", "203.0.113.5:443/https") in assets
    assert result.assets_by_type.get("domain", 0) >= 3


@pytest.mark.asyncio
async def test_rdap_cap_limits_probes(tmp_path):
    scope = _scope(authorized_ips=("203.0.113.5", "203.0.113.6",
                                   "203.0.113.7", "203.0.113.8"))
    db = _setup(tmp_path, scope)
    fofa = StubFofa([
        {"host": "www.example.com", "ip": "203.0.113.5", "port": 80, "protocol": "http"},
    ])
    dns = StubDns({
        "a.example.com": ["203.0.113.6"],
        "b.example.com": ["203.0.113.7"],
        "c.example.com": ["203.0.113.8"],
    })
    rdap = StubRdap()

    async def run():
        return await run_full_passive_recon(
            db, task_id="t", scope=scope,
            fofa_client=fofa, ct_client=StubCt([]), dns_client=dns,
            rdap_client=rdap, fofa_query='domain="example.com"',
            dns_hosts=["www.example.com", "a.example.com", "b.example.com",
                       "c.example.com"],
            max_rdap_ips=2,
        )

    result = await run()
    assert len(result.discovered_ips) == 2  # capped
    assert len(rdap.calls) == 2
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_unconfirmed_scope_blocks_before_any_source(tmp_path):
    scope = _scope(confirmed=False)
    db = _setup(tmp_path, scope)
    result = await run_full_passive_recon(
        db, task_id="t", scope=scope,
        fofa_client=StubFofa([]), ct_client=StubCt([]),
        dns_client=StubDns({}), rdap_client=StubRdap(),
        fofa_query='domain="example.com"',
    )
    assert result.status == "blocked"
    assert result.error == "scope_not_confirmed"
    assert result.assets_by_type == {}


def test_rerun_is_idempotent_on_assets(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    fofa = StubFofa([{"host": "www.example.com", "ip": "203.0.113.5",
                      "port": 443, "protocol": "https"}])
    dns = StubDns({"www.example.com": ["203.0.113.5"]})

    async def run_twice():
        first = await run_full_passive_recon(
            db, task_id="t", scope=scope,
            fofa_client=StubFofa([{"host": "www.example.com", "ip": "203.0.113.5",
                                   "port": 443, "protocol": "https"}]),
            ct_client=StubCt([]), dns_client=dns, rdap_client=StubRdap(),
            fofa_query='domain="example.com"')
        second = await run_full_passive_recon(
            db, task_id="t", scope=scope,
            fofa_client=StubFofa([{"host": "www.example.com", "ip": "203.0.113.5",
                                   "port": 443, "protocol": "https"}]),
            ct_client=StubCt([]), dns_client=dns, rdap_client=StubRdap(),
            fofa_query='domain="example.com"')
        return first, second

    import asyncio

    first, second = asyncio.run(run_twice())
    assert first.assets_by_type == second.assets_by_type
    # re-merge updates in place, never duplicates
    assert second.merge["assets_created"] == 0
    assert second.merge["assets_updated"] >= 1
