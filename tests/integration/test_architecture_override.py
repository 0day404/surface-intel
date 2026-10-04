"""A4: company sites hosted on third-party ranges - the explicit
--allow-hosted-third-party flag lifts only the plain third_party block,
records an override review item, and leaves cdn/waf/lb/cloud signals blocked."""
import json

import pytest

from surface_intel.policy.scope import ActiveLevel, Scope
from surface_intel.safe_http_runner import run_http_observe
from surface_intel.safe_port_runner import run_port_check
from surface_intel.state.database import Database


class StubHttpClient:
    source_name = "safe_http"

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def observe(self, url, *, method="GET", allowed_hosts=()):
        self.calls.append((url, method, tuple(allowed_hosts)))
        return dict(self.outcome)


def _observed(**overrides):
    outcome = {
        "status": "observed", "http_status": 200, "response_class": "ok",
        "final_url": "https://www.example.com/", "redirect_chain": [],
        "headers": {"server": "nginx"}, "content_type": "text/html",
        "body_sha256": "a" * 64, "body_bytes": 12, "body_truncated": False,
        "tls_sha256": "b" * 64, "response_ms": 20,
    }
    outcome.update(overrides)
    return outcome


def _active_scope(**overrides):
    defaults = dict(authorized_domains=("example.com", "*.example.com"),
                    authorized_ips=("203.0.113.5",), confirmed=True,
                    scope_version="scope-v1", active_testing_allowed=True,
                    active_testing_level=ActiveLevel.SAFE_DISCOVERY)
    defaults.update(overrides)
    return Scope(**defaults)


def _setup(tmp_path, assets=()):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','scope-v1')")
        for asset_type, value, architecture in assets:
            connection.execute(
                """INSERT INTO asset(asset_id,task_id,asset_type,normalized_value,scope_status,architecture_status)
                   VALUES(?,?,?,?,?,?)""",
                (f"asset-{value}", "t", asset_type, value, "confirmed", architecture))
    return db


def _gaps(db):
    return [dict(row) for row in db.rows("SELECT * FROM gap")]


@pytest.mark.asyncio
async def test_third_party_blocks_by_default(tmp_path):
    db = _setup(tmp_path, assets=(("domain", "www.example.com", "third_party"),))
    client = StubHttpClient(_observed())
    result = await run_http_observe(db, client, task_id="t", scope=_active_scope(),
                                    url="https://www.example.com/")
    assert result.status == "blocked"
    assert result.error == "architecture_blocked:third_party"
    assert client.calls == []
    # the review item now carries the remediation paths (A4)
    assert any("scope-amend --add-ips" in gap["reason"] for gap in _gaps(db))


@pytest.mark.asyncio
async def test_override_lifts_third_party_and_records_review_item(tmp_path):
    db = _setup(tmp_path, assets=(("domain", "www.example.com", "third_party"),))
    client = StubHttpClient(_observed())
    result = await run_http_observe(db, client, task_id="t", scope=_active_scope(),
                                    url="https://www.example.com/",
                                    allow_hosted_third_party=True)
    assert result.status == "completed"
    assert result.override_used is True
    assert client.calls != []  # the observation actually happened
    reasons = [gap["reason"] for gap in _gaps(db)]
    assert any(r.startswith("architecture_override:third_party:") for r in reasons)
    assert not any(r.startswith("architecture_blocked") for r in reasons)


@pytest.mark.asyncio
async def test_cdn_signal_stays_blocking_even_with_override(tmp_path):
    db = _setup(tmp_path, assets=(("domain", "www.example.com", "cdn_likely"),))
    client = StubHttpClient(_observed())
    result = await run_http_observe(db, client, task_id="t", scope=_active_scope(),
                                    url="https://www.example.com/",
                                    allow_hosted_third_party=True)
    assert result.status == "blocked"
    assert result.error == "architecture_blocked:cdn_likely"
    assert client.calls == []


@pytest.mark.asyncio
async def test_port_check_override_lifts_third_party(tmp_path):
    db = _setup(tmp_path, assets=(("ip", "203.0.113.5", "third_party"),))
    scope = Scope(authorized_domains=("example.com",), authorized_ips=("203.0.113.5",),
                  allowed_ports=(443,), confirmed=True, scope_version="scope-v1",
                  active_testing_allowed=True, active_testing_level=ActiveLevel.SAFE_DISCOVERY)

    class StubConnector:
        def __init__(self):
            self.calls = []

        async def probe(self, host, port):
            self.calls.append((host, port))
            return {"transport_status": "open", "tls_sha256": "a" * 64}

    connector = StubConnector()
    result = await run_port_check(db, connector, task_id="t", scope=scope,
                                  target="203.0.113.5", port=443,
                                  allow_hosted_third_party=True)
    assert result.status == "completed"
    assert result.override_used is True
    assert connector.calls == [("203.0.113.5", 443)]
