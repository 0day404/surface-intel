import json

import pytest

from surface_intel.dns_runner import run_dns_lookup
from surface_intel.policy.scope import Scope
from surface_intel.state.database import Database


class FixtureDnsClient:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def resolve(self, name, record_type):
        self.calls.append((name, record_type))
        outcome = self.answers.get((name, record_type))
        if outcome is None:
            return {"status": "nxdomain", "records": []}
        return dict(outcome)


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


def _payload(db):
    rows = list(db.rows("SELECT payload_json FROM observation ORDER BY observation_id"))
    return [json.loads(row["payload_json"]) for row in rows]


@pytest.mark.asyncio
async def test_in_scope_lookup_persists_observations_and_evidence(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureDnsClient({
        ("www.example.com", "A"): {"status": "observed", "records": ["93.184.216.34"], "ttl": 300,
                                    "resolver": "fixture", "resolved_at": "t0", "resolution_ms": 5},
        ("www.example.com", "CNAME"): {"status": "empty", "records": []},
    })
    result = await run_dns_lookup(
        db, client, task_id="t", scope=scope, domain="www.example.com", record_types=["A", "CNAME"]
    )
    assert result.status == "completed" and result.observations == 2
    payloads = _payload(db)
    assert all(item["source"] == "dns_resolver" for item in payloads)
    assert all(item["scope_version"] == "scope-v1" for item in payloads)
    assert all(item["source_quality"] == "direct" for item in payloads)
    assert all(item["license_status"] == "public_protocol" for item in payloads)
    assert all(item["observed_at"] for item in payloads)
    evidence = list(db.rows("SELECT source FROM evidence"))
    assert len(evidence) == 2
    assert next(db.rows("SELECT COUNT(*) AS count FROM asset"))["count"] == 0


@pytest.mark.asyncio
async def test_resolved_ip_only_confirmed_when_ip_is_authorized(tmp_path):
    scope = _scope(authorized_ips=("93.184.216.34",))
    db = _setup(tmp_path, scope)
    client = FixtureDnsClient({
        ("www.example.com", "A"): {"status": "observed", "records": ["93.184.216.34", "198.51.100.7"]},
    })
    await run_dns_lookup(db, client, task_id="t", scope=scope, domain="www.example.com", record_types=["A"])
    related = _payload(db)[0]["related_scope_status"]
    assert related == "candidate"  # one IP is outside the authorized list

    full_scope = _scope(authorized_ips=("93.184.216.34", "198.51.100.7"))
    (tmp_path / "second").mkdir()
    db2 = _setup(tmp_path / "second", full_scope)
    client2 = FixtureDnsClient({
        ("www.example.com", "A"): {"status": "observed", "records": ["93.184.216.34", "198.51.100.7"]},
    })
    await run_dns_lookup(db2, client2, task_id="t", scope=full_scope, domain="www.example.com", record_types=["A"])
    payload = list(db2.rows("SELECT payload_json FROM observation"))
    assert json.loads(payload[0]["payload_json"])["related_scope_status"] == "confirmed"


@pytest.mark.asyncio
async def test_cname_target_scope_classified_without_recursive_resolution(tmp_path):
    scope = _scope(authorized_domains=("www.example.com", "partner.example.com"))
    db = _setup(tmp_path, scope)
    client = FixtureDnsClient({
        ("www.example.com", "CNAME"): {"status": "observed", "records": ["partner.example.com"]},
        ("www.example.com", "A"): {"status": "observed", "records": ["203.0.113.9"]},
    })
    result = await run_dns_lookup(
        db, client, task_id="t", scope=scope, domain="www.example.com", record_types=["CNAME", "A"]
    )
    assert result.status == "completed"
    payloads = _payload(db)
    by_type = {item["record_type"]: item for item in payloads}
    assert by_type["CNAME"]["related_scope_status"] == "confirmed"
    assert by_type["A"]["related_scope_status"] == "candidate"
    # only the authorized name was ever queried - the CNAME target was not followed
    assert set(client.calls) == {("www.example.com", "CNAME"), ("www.example.com", "A")}


@pytest.mark.asyncio
async def test_all_nxdomain_still_completes(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureDnsClient({})
    result = await run_dns_lookup(db, client, task_id="t", scope=scope, domain="gone.example.com")
    assert result.status == "completed"
    assert all(item["status"] == "nxdomain" for item in _payload(db))


@pytest.mark.asyncio
async def test_single_type_failure_isolated_yields_partial(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureDnsClient({
        ("www.example.com", "A"): {"status": "observed", "records": ["93.184.216.34"]},
        ("www.example.com", "SOA"): {"status": "servfail", "records": []},
    })
    result = await run_dns_lookup(
        db, client, task_id="t", scope=scope, domain="www.example.com", record_types=["A", "SOA", "TXT"]
    )
    assert result.status == "partial"
    statuses = {item["record_type"]: item["status"] for item in _payload(db)}
    assert statuses["A"] == "observed"
    assert statuses["SOA"] == "servfail"
    assert statuses["TXT"] == "nxdomain"  # SOA failure did not stop TXT


@pytest.mark.asyncio
async def test_budget_row_bounds_total_dns_queries(tmp_path):
    from surface_intel.state.budget import BudgetRepository

    scope = _scope()
    db = _setup(tmp_path, scope)
    BudgetRepository(db).create("t", "dns_resolver", 1)
    client = FixtureDnsClient({
        ("www.example.com", "A"): {"status": "observed", "records": ["93.184.216.34"]},
        ("www.example.com", "AAAA"): {"status": "observed", "records": ["2001:db8::1"]},
    })
    result = await run_dns_lookup(
        db, client, task_id="t", scope=scope, domain="www.example.com", record_types=["A", "AAAA"]
    )
    assert result.status == "partial"
    assert result.error == "budget_exhausted"
    assert len(_payload(db)) == 1  # only the budgeted query persisted
    row = next(db.rows("SELECT * FROM budget WHERE task_id='t'"))
    assert row["consumed_units"] == 1


@pytest.mark.asyncio
async def test_actions_audited_in_adapter_call_table(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureDnsClient({
        ("www.example.com", "A"): {"status": "observed", "records": ["93.184.216.34"]},
    })
    await run_dns_lookup(db, client, task_id="t", scope=scope, domain="www.example.com", record_types=["A"])
    calls = list(db.rows("SELECT adapter_name,status FROM adapter_call"))
    assert len(calls) == 1
    assert calls[0]["adapter_name"] == "dns_resolver"
    assert calls[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_partial_reports_which_type_and_resolver_failed(tmp_path):
    """B2: a bare 'timeout' used to hide which (record_type, resolver) pair
    was lost; the structured failed list names them."""
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureDnsClient({
        ("www.example.com", "A"): {"status": "observed", "records": ["93.184.216.34"]},
        ("www.example.com", "SOA"): {"status": "timeout", "records": [],
                                     "resolver": "dns.example.net"},
    })
    result = await run_dns_lookup(
        db, client, task_id="t", scope=scope, domain="www.example.com", record_types=["A", "SOA"]
    )
    assert result.status == "partial"
    assert len(result.failed) == 1
    assert result.failed[0]["record_type"] == "SOA"
    assert result.failed[0]["resolver"] == "dns.example.net"
    assert result.failed[0]["error"] == "timeout"


@pytest.mark.asyncio
async def test_blocked_gate_without_observations_still_records_lost_type(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    from surface_intel.state.budget import BudgetRepository

    BudgetRepository(db).create("t", "dns_resolver", 1)
    client = FixtureDnsClient({
        ("www.example.com", "A"): {"status": "observed", "records": ["93.184.216.34"]},
    })
    result = await run_dns_lookup(
        db, client, task_id="t", scope=scope, domain="www.example.com", record_types=["A", "AAAA"]
    )
    assert result.error == "budget_exhausted"
    assert [item["record_type"] for item in result.failed] == ["AAAA"]
    assert result.failed[0]["resolver"] == "unknown"
