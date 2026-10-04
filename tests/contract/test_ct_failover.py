import json

import pytest

from surface_intel.adapters.base import TaskContext
from surface_intel.adapters.ct import CertificateTransparencyAdapter
from surface_intel.ct_client import (
    CrtShClient,
    CrtlogsDevClient,
    FailoverCtClient,
    configured_ct_client,
)
from surface_intel.ct_runner import run_ct_search
from surface_intel.policy.scope import Scope
from surface_intel.state.database import Database


class ScriptedClient:
    """Records searches; returns a fixed outcome or raises."""

    source_name = "script"

    def __init__(self, outcome=None, exc=None):
        self.outcome = outcome or {}
        self.exc = exc
        self.calls = []

    async def search(self, domain):
        self.calls.append(domain)
        if self.exc is not None:
            raise self.exc
        return dict(self.outcome)

    async def health(self):
        return {"ok": True, "source": self.source_name}


def _observed(source="primary.test"):
    return {
        "status": "observed",
        "certificates": [{
            "common_name": "example.com", "sans": ["example.com"], "issuer": "i",
            "serial": "aa", "not_before": "n", "not_after": "x",
            "first_seen": "f", "last_seen": "l", "log_id": 1, "log_source": source,
        }],
        "log_source": source,
        "queried_at": "2026-10-03T00:00:00+00:00",
        "query_ms": 10,
    }


@pytest.mark.asyncio
async def test_primary_success_never_touches_fallback():
    primary = ScriptedClient(_observed())
    fallback = ScriptedClient(_observed("fallback.test"))
    outcome = await FailoverCtClient(primary, fallback).search("example.com")
    assert outcome["status"] == "observed"
    assert outcome["log_source"] == "primary.test"
    assert "failover" not in outcome
    assert primary.calls == ["example.com"]
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_definitive_empty_is_trusted_not_refetched():
    """An empty answer is definitive - degradation never re-queries it."""
    primary = ScriptedClient({"status": "empty", "certificates": [], "log_source": "crt.sh"})
    fallback = ScriptedClient(_observed("fallback.test"))
    outcome = await FailoverCtClient(primary, fallback).search("example.com")
    assert outcome["status"] == "empty"
    assert "failover" not in outcome
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_primary_infra_failure_fails_over_with_marker():
    primary = ScriptedClient({"status": "http_error", "certificates": [], "http_code": 502})
    fallback = ScriptedClient(_observed("fallback.test"))
    outcome = await FailoverCtClient(primary, fallback).search("example.com")
    assert outcome["status"] == "observed"
    assert outcome["log_source"] == "fallback.test"
    assert outcome["failover"] == {
        "from": "script",
        "reason": "http_error",
        # D1: the real failure detail (the marker used to read None forever)
        "primary_error": "http_code:502",
        "fallback": "script",
    }
    assert fallback.calls == ["example.com"]


@pytest.mark.asyncio
async def test_primary_rate_limit_and_timeout_fail_over():
    for status in ("rate_limited", "timeout", "invalid_response"):
        primary = ScriptedClient({"status": status, "certificates": []})
        fallback = ScriptedClient(_observed())
        outcome = await FailoverCtClient(primary, fallback).search("example.com")
        assert outcome["status"] == "observed", status
        assert outcome["failover"]["reason"] == status


@pytest.mark.asyncio
async def test_both_failing_returns_fallback_failure_with_marker():
    primary = ScriptedClient({"status": "http_error", "certificates": [], "error": "http_error", "http_code": 502})
    fallback = ScriptedClient({"status": "rate_limited", "certificates": []})
    outcome = await FailoverCtClient(primary, fallback).search("example.com")
    assert outcome["status"] == "rate_limited"  # last error wins, action still fails
    assert outcome["failover"]["reason"] == "http_error"
    assert outcome["failover"]["primary_error"] == "http_error"


@pytest.mark.asyncio
async def test_primary_transport_death_falls_over():
    primary = ScriptedClient(exc=ConnectionResetError())
    fallback = ScriptedClient(_observed())
    outcome = await FailoverCtClient(primary, fallback).search("example.com")
    assert outcome["status"] == "observed"
    assert outcome["failover"]["reason"] == "transport:ConnectionResetError"


@pytest.mark.asyncio
async def test_fallback_transport_death_yields_failed_outcome_with_marker():
    primary = ScriptedClient({"status": "http_error", "certificates": []})
    fallback = ScriptedClient(exc=RuntimeError("dead"))
    outcome = await FailoverCtClient(primary, fallback).search("example.com")
    assert outcome["status"] == "transport:RuntimeError"
    assert outcome["certificates"] == []
    assert outcome["failover"]["fallback"] == "script"


@pytest.mark.asyncio
async def test_health_reports_both_sources_and_strategy():
    outcome = await FailoverCtClient(ScriptedClient(), ScriptedClient()).health()
    assert outcome["strategy"] == "failover"
    assert outcome["source"] == "auto(crt.sh->ctlogs.dev)"
    assert outcome["primary"]["ok"] is True
    assert outcome["fallback"]["ok"] is True


def test_configured_ct_client_default_is_failover(monkeypatch):
    monkeypatch.delenv("SURFACE_INTEL_CT_SOURCE", raising=False)
    client = configured_ct_client()
    assert isinstance(client, FailoverCtClient)
    assert isinstance(client._primary, CrtShClient)
    assert isinstance(client._fallback, CrtlogsDevClient)

    monkeypatch.setenv("SURFACE_INTEL_CT_SOURCE", "auto")
    assert isinstance(configured_ct_client(), FailoverCtClient)

    monkeypatch.setenv("SURFACE_INTEL_CT_SOURCE", "crtsh")
    assert isinstance(configured_ct_client(), CrtShClient)

    monkeypatch.setenv("SURFACE_INTEL_CT_SOURCE", "ctlogs")
    assert isinstance(configured_ct_client(), CrtlogsDevClient)


def test_failover_source_names_are_stable():
    assert CrtShClient().source_name == "crt.sh"
    assert CrtlogsDevClient().source_name == "ctlogs.dev"


@pytest.mark.asyncio
async def test_adapter_persists_failover_marker_on_success():
    outcome = _observed("ctlogs.dev")
    outcome["failover"] = {"from": "crt.sh", "reason": "http_error", "primary_error": None,
                           "fallback": "ctlogs.dev"}
    result = await CertificateTransparencyAdapter(ScriptedClient(outcome)).execute(
        {"target": "example.com"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "completed"
    assert result.observations[0]["failover"]["from"] == "crt.sh"
    assert result.observations[0]["log_source"] == "ctlogs.dev"


@pytest.mark.asyncio
async def test_adapter_persists_failover_marker_on_infra_failure():
    outcome = {"status": "rate_limited", "certificates": [], "log_source": "ctlogs.dev",
               "failover": {"from": "crt.sh", "reason": "http_error", "primary_error": None,
                            "fallback": "ctlogs.dev"}}
    result = await CertificateTransparencyAdapter(ScriptedClient(outcome)).execute(
        {"target": "example.com"}, TaskContext("task-1", "scope-v1")
    )
    assert result.status == "failed"
    assert result.observations[0]["failover"]["fallback"] == "ctlogs.dev"


def _setup(tmp_path, scope):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0',?)",
            (scope.scope_version,),
        )
    return db


@pytest.mark.asyncio
async def test_failover_marker_flows_through_runner_storage(tmp_path):
    scope = Scope(authorized_domains=("example.com", "*.example.com"), confirmed=True,
                  scope_version="scope-v1")
    db = _setup(tmp_path, scope)
    outcome = _observed("ctlogs.dev")
    outcome["failover"] = {"from": "crt.sh", "reason": "http_error", "primary_error": None,
                           "fallback": "ctlogs.dev"}
    result = await run_ct_search(db, FailoverLikeClient(outcome), task_id="t", scope=scope,
                                 domain="example.com")
    assert result.status == "completed"
    payload = json.loads(list(db.rows("SELECT payload_json FROM observation"))[0]["payload_json"])
    assert payload["failover"] == outcome["failover"]
    assert payload["log_source"] == "ctlogs.dev"


class FailoverLikeClient:
    """Runner-level stub returning a pre-built failover-marked outcome."""

    def __init__(self, outcome):
        self.outcome = outcome

    async def search(self, domain):
        return dict(self.outcome)
