import json

import pytest

from surface_intel.policy.scope import Scope
from surface_intel.rdap_runner import run_rdap_lookup
from surface_intel.state.database import Database


class FixtureRdapClient:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def lookup(self, target, kind):
        self.calls.append((target, kind))
        outcome = self.answers.get((target, kind))
        if outcome is None:
            return {"status": "not_found", "source_protocol": "rdap"}
        return dict(outcome)


def _record(**overrides):
    row = {
        "handle": "NET-203-0-113-0-1",
        "name": "EXAMPLE-NET",
        "country": "US",
        "organizations": ["Hosting Provider Inc"],
        "registrar": None,
        "abuse_contacts": ["abuse@hosting.example"],
        "events": {"registration": "2021-01-01T00:00:00", "expiration": None, "last_changed": None},
        "cidrs": ["203.0.113/24"],
        "ip_version": "v4",
        "start_address": "203.0.113.0",
        "end_address": "203.0.113.255",
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
    rows = list(db.rows("SELECT payload_json FROM observation ORDER BY rowid"))
    return [json.loads(row["payload_json"]) for row in rows]


@pytest.mark.asyncio
async def test_ip_lookup_persists_registration_observation_and_evidence(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureRdapClient({
        ("203.0.113.5", "ip"): {"status": "observed", "record": _record(), "source_protocol": "rdap"},
    })
    result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="203.0.113.5", kind="ip")
    assert result.status == "completed"
    assert result.observations == 1
    payload = _payload(db)[0]
    assert payload["source"] == "rdap_lookup"
    assert payload["target"] == "203.0.113.5"
    assert payload["target_kind"] == "ip"
    assert payload["scope_version"] == "scope-v1"
    assert payload["source_quality"] == "direct"
    assert payload["license_status"] == "public_rdap"
    assert payload["related_scope_status"] == "candidate"
    evidence = list(db.rows("SELECT source,redaction_status FROM evidence"))
    assert len(evidence) == 1 and evidence[0]["source"] == "rdap_lookup"
    assert next(db.rows("SELECT COUNT(*) AS count FROM asset"))["count"] == 0


@pytest.mark.asyncio
async def test_domain_lookup_and_not_found_classification(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureRdapClient({
        ("www.example.com", "domain"): {"status": "observed",
                                        "record": _record(name="www.example.com", cidrs=[]),
                                        "source_protocol": "rdap"},
    })
    result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="www.example.com", kind="domain")
    assert result.status == "completed"
    assert _payload(db)[0]["related_scope_status"] == "candidate"

    client2 = FixtureRdapClient({})
    result2 = await run_rdap_lookup(db, client2, task_id="t", scope=scope, target="gone.example.com", kind="domain")
    assert result2.status == "completed"
    assert _payload(db)[-1]["status"] == "not_found"
    assert _payload(db)[-1]["related_scope_status"] == "not_applicable"


@pytest.mark.asyncio
async def test_whois_fallback_marked_with_degraded_quality_and_license(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureRdapClient({
        ("example.com", "domain"): {
            "status": "observed",
            "record": {**_record(name="example.com", cidrs=[]), "data_quality": "whois_fallback"},
            "source_protocol": "whois",
        },
    })
    result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="example.com", kind="domain")
    assert result.status == "completed"
    payload = _payload(db)[0]
    assert payload["source_quality"] == "whois_fallback"
    assert payload["license_status"] == "whois_text_unverified"
    assert payload["record"]["data_quality"] == "whois_fallback"


@pytest.mark.asyncio
async def test_infrastructure_failure_still_records_failed_observation(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureRdapClient({
        ("203.0.113.5", "ip"): {"status": "rate_limited"},
    })
    result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="203.0.113.5", kind="ip")
    assert result.status == "failed"
    assert result.error == "rate_limited"
    payload = _payload(db)[0]
    assert payload["status"] == "rate_limited"
    calls = list(db.rows("SELECT status,error_code FROM adapter_call"))
    assert calls[0]["status"] == "failed"
    assert calls[0]["error_code"] == "rate_limited"


@pytest.mark.asyncio
async def test_budget_row_bounds_rdap_queries(tmp_path):
    from surface_intel.state.budget import BudgetRepository

    scope = _scope()
    db = _setup(tmp_path, scope)
    BudgetRepository(db).create("t", "rdap_lookup", 0)
    client = FixtureRdapClient({
        ("203.0.113.5", "ip"): {"status": "observed", "record": _record(), "source_protocol": "rdap"},
    })
    result = await run_rdap_lookup(db, client, task_id="t", scope=scope, target="203.0.113.5", kind="ip")
    assert result.status == "blocked"
    assert result.error == "budget_exhausted"
    assert _payload(db) == []
    assert client.calls == []


@pytest.mark.asyncio
async def test_action_audited_in_adapter_call_table(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureRdapClient({
        ("203.0.113.5", "ip"): {"status": "observed", "record": _record(), "source_protocol": "rdap"},
    })
    await run_rdap_lookup(db, client, task_id="t", scope=scope, target="203.0.113.5", kind="ip")
    calls = list(db.rows("SELECT adapter_name,status FROM adapter_call"))
    assert len(calls) == 1
    assert calls[0]["adapter_name"] == "rdap_lookup"
    assert calls[0]["status"] == "completed"
