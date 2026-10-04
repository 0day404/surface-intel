import json

import pytest

from surface_intel.ct_client import CrtShClient, CrtlogsDevClient, FailoverCtClient
from surface_intel.ct_runner import run_ct_search
from surface_intel.policy.scope import Scope
from surface_intel.state.database import Database


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
async def test_search_persists_certificate_observation_and_evidence(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureCtClient({"example.com": {"status": "observed", "certificates": [_cert()]}})
    result = await run_ct_search(db, client, task_id="t", scope=scope, domain="example.com")
    assert result.status == "completed"
    assert result.observations == 1
    assert result.certificates == 1
    payload = _payload(db)[0]
    assert payload["source"] == "ct_log"
    assert payload["scope_version"] == "scope-v1"
    assert payload["source_quality"] == "direct"
    assert payload["license_status"] == "public_ct_log"
    assert payload["observed_at"]
    assert payload["target"] == "example.com"
    assert payload["certificates"][0]["serial"] == "04aabbcc"
    evidence = list(db.rows("SELECT source,redaction_status FROM evidence"))
    assert len(evidence) == 1
    assert evidence[0]["source"] == "ct_log"
    assert evidence[0]["redaction_status"] == "redacted"
    assert next(db.rows("SELECT COUNT(*) AS count FROM asset"))["count"] == 0


@pytest.mark.asyncio
async def test_empty_result_completes_with_not_applicable_classification(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureCtClient({})
    result = await run_ct_search(db, client, task_id="t", scope=scope, domain="gone.example.com")
    assert result.status == "completed"
    assert result.certificates == 0
    payload = _payload(db)[0]
    assert payload["status"] == "empty"
    assert payload["related_scope_status"] == "not_applicable"


@pytest.mark.asyncio
async def test_infrastructure_failure_still_records_failed_observation(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureCtClient({"example.com": {"status": "rate_limited", "certificates": []}})
    result = await run_ct_search(db, client, task_id="t", scope=scope, domain="example.com")
    assert result.status == "failed"
    assert result.error == "rate_limited"
    payload = _payload(db)[0]
    assert payload["status"] == "rate_limited"
    calls = list(db.rows("SELECT status,error_code FROM adapter_call"))
    assert calls[0]["status"] == "failed"
    assert calls[0]["error_code"] == "rate_limited"


@pytest.mark.asyncio
async def test_budget_row_bounds_ct_queries(tmp_path):
    from surface_intel.state.budget import BudgetRepository

    scope = _scope()
    db = _setup(tmp_path, scope)
    BudgetRepository(db).create("t", "ct_log", 0)
    client = FixtureCtClient({"example.com": {"status": "observed", "certificates": [_cert()]}})
    result = await run_ct_search(db, client, task_id="t", scope=scope, domain="example.com")
    assert result.status == "blocked"
    assert result.error == "budget_exhausted"
    assert _payload(db) == []  # blocked before any client call persisted
    assert client.calls == []  # client never invoked


@pytest.mark.asyncio
async def test_actions_audited_in_adapter_call_table(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = FixtureCtClient({"example.com": {"status": "observed", "certificates": [_cert()]}})
    await run_ct_search(db, client, task_id="t", scope=scope, domain="example.com")
    calls = list(db.rows("SELECT adapter_name,status FROM adapter_call"))
    assert len(calls) == 1
    assert calls[0]["adapter_name"] == "ct_log"
    assert calls[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_bare_crtsh_client_gains_failover_via_ensure(tmp_path):
    """Retrospective P0-1: ensure_failover wraps a bare CrtShClient; the
    caller keeps its original object but the returned chain holds."""
    from surface_intel.ct_client import CrtlogsDevClient, FailoverCtClient
    from surface_intel.ct_runner import ensure_failover

    real_client = CrtShClient(timeout=7.5)
    chain = ensure_failover(real_client)
    assert isinstance(chain, FailoverCtClient)
    assert chain._primary is real_client
    assert isinstance(chain._fallback, CrtlogsDevClient)
    assert chain._fallback.timeout == 7.5  # timeout carried over
    # idempotent: wrapping the chain again returns it unchanged
    assert ensure_failover(chain) is chain


@pytest.mark.asyncio
async def test_primary_failure_falls_back_to_injected_client(tmp_path):
    """Behavioral: when the bare primary 502s, the injected fallback answers
    and the run still completes."""
    from surface_intel.ct_client import CrtShClient
    from surface_intel.ct_runner import run_ct_search

    scope = _scope()
    db = _setup(tmp_path, scope)
    failing_primary = CrtShClient(fetch=lambda url, timeout: (_ for _ in ()).throw(
        __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
            "u", 502, "bad gateway", None, None)))
    class StubFallback:
        def __init__(self):
            self.calls = []

        async def search(self, domain):
            self.calls.append(domain)
            return {"status": "observed", "log_source": "ctlogs.dev", "certificates": []}

    fallback = StubFallback()
    result = await run_ct_search(db, failing_primary, task_id="t", scope=scope,
                                 domain="www.example.com",
                                 failover_client=fallback)
    assert result.status == "completed"
    assert result.observations == 1
    assert fallback.calls == ["www.example.com"]
    payload = _payload(db)[0]
    assert payload["log_source"] == "ctlogs.dev"


