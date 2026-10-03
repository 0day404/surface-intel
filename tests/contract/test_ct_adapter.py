import pytest

from surface_intel.adapters.base import TaskContext
from surface_intel.adapters.ct import CertificateTransparencyAdapter


class FixtureCtClient:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def search(self, domain):
        self.calls.append(domain)
        outcome = self.answers.get(domain)
        if outcome is None:
            return {"status": "empty", "certificates": []}
        return dict(outcome)


def _cert(**overrides):
    row = {
        "common_name": "www.example.com",
        "sans": ["www.example.com", "example.com"],
        "issuer": "C=US, O=Let's Encrypt, CN=E5",
        "serial": "04aabbcc",
        "not_before": "2026-01-01T00:00:00",
        "not_after": "2026-04-01T00:00:00",
        "first_seen": "2026-01-02T00:00:00.000",
        "last_seen": "2026-01-02T00:00:00.000",
        "log_id": 1234567890,
        "log_source": "crt.sh",
    }
    row.update(overrides)
    return row


def _observed(certificates):
    return {
        "status": "observed",
        "certificates": certificates,
        "log_source": "crt.sh",
        "queried_at": "2026-10-03T00:00:00+00:00",
        "query_ms": 120,
    }


@pytest.mark.asyncio
async def test_observed_search_maps_certificate_fields():
    client = FixtureCtClient({"example.com": _observed([_cert()])})
    result = await CertificateTransparencyAdapter(client).execute(
        {"target": "example.com"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "completed"
    assert result.cost_units == 1
    assert result.request_id == "ct-task-1-example.com"
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation["target"] == "example.com"
    assert observation["matched_count"] == 1
    assert observation["source"] == "ct_log"
    assert observation["log_source"] == "crt.sh"
    assert observation["status"] == "observed"
    cert = observation["certificates"][0]
    assert cert["common_name"] == "www.example.com"
    assert cert["sans"] == ["www.example.com", "example.com"]
    assert cert["issuer"].startswith("C=US")
    assert cert["serial"] == "04aabbcc"
    assert cert["not_before"] == "2026-01-01T00:00:00"
    assert cert["not_after"] == "2026-04-01T00:00:00"
    assert cert["first_seen"] == "2026-01-02T00:00:00.000"
    assert cert["last_seen"] == "2026-01-02T00:00:00.000"
    assert cert["log_id"] == 1234567890


@pytest.mark.asyncio
async def test_empty_search_is_definitive_completion():
    client = FixtureCtClient({"example.com": {"status": "empty", "certificates": []}})
    result = await CertificateTransparencyAdapter(client).execute(
        {"target": "example.com"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "completed"
    assert result.observations[0]["status"] == "empty"
    assert result.observations[0]["certificates"] == []


@pytest.mark.asyncio
async def test_rate_limited_and_http_error_are_infrastructure_failures():
    client = FixtureCtClient({"example.com": {"status": "rate_limited", "certificates": []}})
    result = await CertificateTransparencyAdapter(client).execute(
        {"target": "example.com"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "failed" and result.error == "rate_limited"
    assert result.observations[0]["status"] == "rate_limited"

    http_client = FixtureCtClient({"example.com": {"status": "http_error", "certificates": [], "http_code": 503}})
    http_result = await CertificateTransparencyAdapter(http_client).execute(
        {"target": "example.com"}, TaskContext("task-1", "scope-v1")
    )
    assert http_result.status == "failed" and http_result.error == "http_error"


@pytest.mark.asyncio
async def test_timeout_maps_to_timed_out_status():
    class TimeoutClient:
        async def search(self, domain):
            raise TimeoutError

    result = await CertificateTransparencyAdapter(TimeoutClient()).execute(
        {"target": "example.com"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "timed_out"
    assert result.observations[0]["status"] == "timeout"


@pytest.mark.asyncio
async def test_transport_death_is_coarse_failure():
    class DeadClient:
        async def search(self, domain):
            raise ConnectionError("reset")

    result = await CertificateTransparencyAdapter(DeadClient()).execute(
        {"target": "example.com"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "failed"
    assert result.error == "ct:ConnectionError"


@pytest.mark.asyncio
async def test_unknown_client_status_rejected():
    client = FixtureCtClient({"example.com": {"status": "weird", "certificates": []}})
    result = await CertificateTransparencyAdapter(client).execute(
        {"target": "example.com"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "failed"
    assert result.error == "invalid_client_status:weird"


@pytest.mark.asyncio
async def test_missing_target_blocked_in_validate():
    adapter = CertificateTransparencyAdapter(FixtureCtClient({}))
    result = await adapter.validate({"target": "  "}, TaskContext("task-1", "scope-v1"))
    assert result.status == "blocked" and result.error == "target_required"
    ok = await adapter.validate({"target": "example.com"}, TaskContext("task-1", "scope-v1"))
    assert ok.status == "completed"


@pytest.mark.asyncio
async def test_alternate_source_completeness_markers_pass_through():
    """ctlogs.dev rows carry san_count/san_completeness; crt.sh rows leave them None."""
    ctlogs_row = _cert(log_source="ctlogs.dev", san_count=2, san_completeness="match_only")
    client = FixtureCtClient({"example.com": _observed([ctlogs_row, _cert()])})
    result = await CertificateTransparencyAdapter(client).execute(
        {"target": "example.com"}, TaskContext("task-1", "scope-v1")
    )
    rows = result.observations[0]["certificates"]
    assert rows[0]["san_count"] == 2
    assert rows[0]["san_completeness"] == "match_only"
    assert rows[0]["log_source"] == "ctlogs.dev"
    assert rows[1]["san_count"] is None  # crt.sh rows have no completeness markers
    assert rows[1]["san_completeness"] is None


@pytest.mark.asyncio
async def test_health_without_client_reports_not_configured():
    result = await CertificateTransparencyAdapter(FixtureCtClient({})).health(
        TaskContext("task-1", "scope-v1")
    )
    assert result.status == "unknown"
    assert result.error == "not_configured:ct_client_health_unavailable"
