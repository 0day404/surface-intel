import pytest

from surface_intel.adapters.base import TaskContext
from surface_intel.adapters.rdap import TARGET_KINDS, RdapAdapter


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
        "handle": "D1234-CNNIC",
        "name": "example.com",
        "country": "CN",
        "organizations": ["Example Org Ltd"],
        "registrar": "Example Registrar Co.",
        "abuse_contacts": ["abuse@registrar.example"],
        "events": {"registration": "2020-01-01T00:00:00", "expiration": "2027-01-01T00:00:00",
                   "last_changed": "2026-01-01T00:00:00"},
        "cidrs": [],
        "statuses": ["client transfer prohibited"],
        "nameservers": ["ns1.example.com"],
    }
    row.update(overrides)
    return row


def _observed(record, protocol="rdap"):
    return {
        "status": "observed",
        "record": record,
        "source_protocol": protocol,
        "queried_at": "2026-10-03T00:00:00+00:00",
        "query_ms": 90,
    }


@pytest.mark.asyncio
async def test_observed_lookup_maps_registration_record():
    client = FixtureRdapClient({("example.com", "domain"): _observed(_record())})
    result = await RdapAdapter(client).execute(
        {"target": "example.com", "kind": "domain"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "completed"
    assert result.cost_units == 1
    assert result.request_id == "rdap-task-1-domain-example.com"
    observation = result.observations[0]
    assert observation["target"] == "example.com"
    assert observation["target_kind"] == "domain"
    assert observation["source"] == "rdap_lookup"
    assert observation["source_protocol"] == "rdap"
    assert observation["status"] == "observed"
    record = observation["record"]
    assert record["handle"] == "D1234-CNNIC"
    assert record["registrar"] == "Example Registrar Co."
    assert record["organizations"] == ["Example Org Ltd"]
    assert record["abuse_contacts"] == ["abuse@registrar.example"]
    assert record["events"]["registration"] == "2020-01-01T00:00:00"
    assert record["events"]["expiration"] == "2027-01-01T00:00:00"
    assert record["nameservers"] == ["ns1.example.com"]


@pytest.mark.asyncio
async def test_not_found_is_definitive_completion():
    client = FixtureRdapClient({("203.0.113.5", "ip"): {"status": "not_found", "source_protocol": "rdap"}})
    result = await RdapAdapter(client).execute(
        {"target": "203.0.113.5", "kind": "ip"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "completed"
    assert result.observations[0]["status"] == "not_found"


@pytest.mark.asyncio
async def test_infrastructure_statuses_fail_the_action():
    for status in ("rate_limited", "http_error", "unsupported", "invalid_response"):
        client = FixtureRdapClient({("example.com", "domain"): {"status": status}})
        result = await RdapAdapter(client).execute(
            {"target": "example.com", "kind": "domain"}, TaskContext("task-1", "scope-v1")
        )
        assert result.status == "failed", status
        assert result.error == status
        assert result.observations[0]["status"] == status


@pytest.mark.asyncio
async def test_timeout_maps_to_timed_out_status():
    class TimeoutClient:
        async def lookup(self, target, kind):
            raise TimeoutError

    result = await RdapAdapter(TimeoutClient()).execute(
        {"target": "example.com", "kind": "domain"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "timed_out"
    assert result.observations[0]["status"] == "timeout"


@pytest.mark.asyncio
async def test_transport_death_is_coarse_failure():
    class DeadClient:
        async def lookup(self, target, kind):
            raise ConnectionResetError

    result = await RdapAdapter(DeadClient()).execute(
        {"target": "example.com", "kind": "domain"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "failed"
    assert result.error == "rdap:ConnectionResetError"


@pytest.mark.asyncio
async def test_unknown_client_status_rejected():
    client = FixtureRdapClient({("example.com", "domain"): {"status": "weird"}})
    result = await RdapAdapter(client).execute(
        {"target": "example.com", "kind": "domain"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "failed"
    assert result.error == "invalid_client_status:weird"


@pytest.mark.asyncio
async def test_validate_requires_target_and_whitelisted_kind():
    adapter = RdapAdapter(FixtureRdapClient({}))
    missing = await adapter.validate({"target": " ", "kind": "domain"}, TaskContext("t", "v"))
    assert missing.status == "blocked" and missing.error == "target_required"
    bad_kind = await adapter.validate({"target": "example.com", "kind": "hostname"}, TaskContext("t", "v"))
    assert bad_kind.status == "blocked" and bad_kind.error == "invalid_target_kind"
    assert "asn" in TARGET_KINDS
    ok = await adapter.validate({"target": "example.com", "kind": "domain"}, TaskContext("t", "v"))
    assert ok.status == "completed"


@pytest.mark.asyncio
async def test_health_without_client_reports_not_configured():
    result = await RdapAdapter(FixtureRdapClient({})).health(TaskContext("task-1", "scope-v1"))
    assert result.status == "unknown"
    assert result.error == "not_configured:rdap_client_health_unavailable"
