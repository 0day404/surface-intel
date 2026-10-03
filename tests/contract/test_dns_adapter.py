import pytest

from surface_intel.adapters.base import TaskContext
from surface_intel.adapters.capabilities import CapabilityRegistry
from surface_intel.adapters.dns import DNS_RECORD_TYPES, DnsAdapter


class FixtureDnsClient:
    def __init__(self, answers, calls=None):
        self.answers = answers
        self.calls = calls if calls is not None else []

    async def resolve(self, name, record_type):
        self.calls.append((name, record_type))
        outcome = self.answers.get((name, record_type.upper()))
        if outcome is None:
            return {"status": "nxdomain", "records": []}
        return dict(outcome)


def _observed(records, ttl=300, dnssec=None, resolver="fixture"):
    return {"status": "observed", "records": records, "ttl": ttl, "resolver": resolver,
            "resolved_at": "2026-10-03T00:00:00+00:00", "resolution_ms": 12, "dnssec": dnssec}


@pytest.mark.asyncio
async def test_all_record_types_resolve_into_structured_observations():
    answers = {
        ("example.com", "A"): _observed(["93.184.216.34"]),
        ("example.com", "AAAA"): _observed(["2606:2800:220:1:248:1893:25c8:1946"]),
        ("example.com", "CNAME"): _observed(["cdn.example.net"]),
        ("example.com", "NS"): _observed(["ns1.example.com"]),
        ("example.com", "MX"): _observed(["10 mail.example.com"]),
        ("example.com", "TXT"): _observed(["v=spf1 -all"]),
        ("example.com", "SOA"): _observed(["ns1.example.com hostmaster.example.com 2026010101"]),
    }
    client = FixtureDnsClient(answers)
    result = await DnsAdapter(client).execute(
        {"target": "example.com"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "completed"
    assert [item["record_type"] for item in result.observations] == list(DNS_RECORD_TYPES)
    for item in result.observations:
        assert item["status"] == "observed"
        assert item["ttl"] == 300
        assert item["resolver"] == "fixture"
        assert item["source"] == "dns_resolver"
    assert result.cost_units == len(DNS_RECORD_TYPES)
    assert result.request_id.startswith("dns-task-1-")


@pytest.mark.asyncio
async def test_dnssec_ad_bit_is_recorded_verbatim():
    client = FixtureDnsClient({("example.com", "A"): _observed(["93.184.216.34"], dnssec=True)})
    result = await DnsAdapter(client).execute(
        {"target": "example.com", "record_types": ["A"]}, TaskContext("t", "s")
    )
    assert result.observations[0]["dnssec"] is True


@pytest.mark.asyncio
async def test_nxdomain_and_empty_are_completed_negative_observations():
    client = FixtureDnsClient({
        ("missing.example.com", "A"): {"status": "nxdomain", "records": []},
        ("missing.example.com", "TXT"): {"status": "empty", "records": []},
    })
    result = await DnsAdapter(client).execute(
        {"target": "missing.example.com", "record_types": ["A", "TXT"]}, TaskContext("t", "s")
    )
    assert result.status == "completed"
    assert [item["status"] for item in result.observations] == ["nxdomain", "empty"]


@pytest.mark.asyncio
async def test_servfail_fails_action_and_keeps_infra_observation():
    client = FixtureDnsClient({("example.com", "A"): {"status": "servfail", "records": []}})
    result = await DnsAdapter(client).execute(
        {"target": "example.com", "record_types": ["A", "AAAA"]}, TaskContext("t", "s")
    )
    assert result.status == "failed"
    assert result.error == "servfail"
    assert result.observations[0]["status"] == "servfail"
    # the AAAA query after a servfail is never dispatched
    assert ("example.com", "AAAA") not in client.calls


@pytest.mark.asyncio
async def test_timeout_maps_to_timed_out_status():
    client = FixtureDnsClient({("example.com", "A"): {"status": "timeout", "records": []}})
    result = await DnsAdapter(client).execute(
        {"target": "example.com", "record_types": ["A"]}, TaskContext("t", "s")
    )
    assert result.status == "timed_out"
    assert result.error == "timeout"


@pytest.mark.asyncio
async def test_transport_exception_is_classified_remote_error():
    class ExplodingClient:
        async def resolve(self, name, record_type):
            raise RuntimeError("socket gone")

    result = await DnsAdapter(ExplodingClient()).execute(
        {"target": "example.com", "record_types": ["A"]}, TaskContext("t", "s")
    )
    assert result.status == "failed"
    assert result.error == "dns:RuntimeError"


@pytest.mark.asyncio
async def test_validate_rejects_missing_target_and_unknown_record_types():
    adapter = DnsAdapter(FixtureDnsClient({}))
    context = TaskContext("t", "s")
    missing = await adapter.validate({"record_types": ["A"]}, context)
    assert missing.status == "blocked" and missing.error == "target_required"
    zone_transfer = await adapter.validate({"target": "example.com", "record_types": ["AXFR"]}, context)
    assert zone_transfer.status == "blocked" and zone_transfer.error == "invalid_record_type"
    wildcard = await adapter.validate({"target": "example.com", "record_types": ["ANY"]}, context)
    assert wildcard.status == "blocked" and wildcard.error == "invalid_record_type"


@pytest.mark.asyncio
async def test_health_delegates_to_client_without_target_queries():
    class HealthyClient:
        def __init__(self):
            self.calls = []

        async def resolve(self, name, record_type):
            self.calls.append((name, record_type))
            return {"status": "nxdomain", "records": []}

        async def health(self):
            return {"ok": True, "resolvers": ["10.0.0.1"]}

    client = HealthyClient()
    result = await DnsAdapter(client).health(TaskContext("t", "s"))
    assert result.status == "completed"
    assert result.observations[0]["resolvers"] == ["10.0.0.1"]
    assert client.calls == []  # health never touches target names


def test_manifest_registers_as_passive_capability():
    registry = CapabilityRegistry([DnsAdapter(FixtureDnsClient({})).manifest])
    check = registry.check(
        "dns_resolver", capability="dns_resolve",
        active_level="passive_only", scope_confirmed=True,
    )
    assert check.ok
