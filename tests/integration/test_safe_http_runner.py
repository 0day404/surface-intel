"""R6.1 Safe HTTP runner: active authorization, architecture gate with review
items, redirect scope classification and digest-only storage."""
import json

import pytest

from surface_intel.policy.scope import Scope, ActiveLevel
from surface_intel.safe_http_runner import run_http_observe
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
        "status": "observed",
        "http_status": 200,
        "response_class": "ok",
        "final_url": "https://www.example.com/",
        "redirect_chain": [{"url": "https://www.example.com/", "http_status": 200}],
        "headers": {"server": "nginx"},
        "content_type": "text/html",
        "body_sha256": "a" * 64,
        "body_bytes": 12,
        "body_truncated": False,
        "tls_sha256": "b" * 64,
        "response_ms": 20,
    }
    outcome.update(overrides)
    return outcome


def _setup(tmp_path, scope, assets=()):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0',?)",
            (scope.scope_version,),
        )
        for asset_type, value, architecture in assets:
            connection.execute(
                """INSERT INTO asset(asset_id,task_id,asset_type,normalized_value,scope_status,architecture_status)
                   VALUES(?,?,?,?,?,?)""",
                (f"asset-{value}", "t", asset_type, value, "confirmed", architecture),
            )
    return db


def _active_scope(**overrides):
    defaults = dict(
        authorized_domains=("example.com", "*.example.com"),
        confirmed=True,
        scope_version="scope-v1",
        active_testing_allowed=True,
        active_testing_level=ActiveLevel.SAFE_DISCOVERY,
    )
    defaults.update(overrides)
    return Scope(**defaults)


def _payload(db):
    return [json.loads(row["payload_json"]) for row in db.rows(
        "SELECT payload_json FROM observation ORDER BY rowid")]


@pytest.mark.asyncio
async def test_happy_path_observation_persisted_with_digest_only(tmp_path):
    scope = _active_scope()
    db = _setup(tmp_path, scope)
    client = StubHttpClient(_observed())
    result = await run_http_observe(db, client, task_id="t", scope=scope,
                                    url="https://www.example.com/")
    assert result.status == "completed"
    assert result.observations == 1
    payload = _payload(db)[0]
    assert payload["source"] == "safe_http"
    assert payload["target"] == "https://www.example.com/"
    assert payload["status_code"] == 200
    assert payload["body_sha256"] == "a" * 64
    assert "body" not in payload or not isinstance(payload.get("body"), str)
    assert payload["related_scope_status"] == "candidate"  # redirect stayed in scope, never confirmed
    assert payload["license_status"] == "public_http"
    # the runner passes the confirmed hosts as the redirect allowlist
    assert ("https://www.example.com/", "GET", ("example.com", "*.example.com")) in client.calls


@pytest.mark.asyncio
async def test_out_of_scope_redirect_classified_needs_review(tmp_path):
    scope = _active_scope()
    db = _setup(tmp_path, scope)
    client = StubHttpClient(_observed(redirect_chain=[
        {"url": "https://www.example.com/", "http_status": 302},
        {"url": "https://tracker.thirdparty.net/p", "http_status": 200},
    ], final_url="https://tracker.thirdparty.net/p"))
    result = await run_http_observe(db, client, task_id="t", scope=scope,
                                    url="https://www.example.com/")
    assert result.status == "completed"
    assert _payload(db)[0]["related_scope_status"] == "needs_review"


@pytest.mark.asyncio
async def test_active_authorization_is_required(tmp_path):
    db = _setup(tmp_path, _active_scope(active_testing_allowed=False,
                                        active_testing_level=ActiveLevel.PASSIVE_ONLY))
    client = StubHttpClient(_observed())
    result = await run_http_observe(db, client, task_id="t", scope=_active_scope(
        active_testing_allowed=False, active_testing_level=ActiveLevel.PASSIVE_ONLY),
        url="https://www.example.com/")
    assert result.status == "blocked"
    assert result.error == "active_testing_not_authorized"
    assert client.calls == []


@pytest.mark.asyncio
async def test_architecture_gate_blocks_probing_and_creates_review_item(tmp_path):
    scope = _active_scope()
    db = _setup(tmp_path, scope, assets=(("domain", "www.example.com", "cdn_likely"),))
    client = StubHttpClient(_observed())
    result = await run_http_observe(db, client, task_id="t", scope=scope,
                                    url="https://www.example.com/")
    assert result.status == "blocked"
    assert result.error == "architecture_blocked:cdn_likely"
    assert result.review_created is True
    gaps = [dict(row) for row in db.rows("SELECT * FROM gap")]
    assert len(gaps) == 1
    assert gaps[0]["priority"] == "high"
    assert "cdn_likely" in gaps[0]["reason"]
    assert client.calls == []  # blocked before any traffic

    # idempotent review item
    again = await run_http_observe(db, client, task_id="t", scope=scope,
                                   url="https://www.example.com/")
    assert again.review_created is False
    assert len([dict(row) for row in db.rows("SELECT * FROM gap")]) == 1


@pytest.mark.asyncio
async def test_unknown_architecture_stays_allowed(tmp_path):
    scope = _active_scope()
    db = _setup(tmp_path, scope, assets=(("domain", "www.example.com", "unknown"),))
    client = StubHttpClient(_observed())
    result = await run_http_observe(db, client, task_id="t", scope=scope,
                                    url="https://www.example.com/")
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_unconfirmed_scope_blocks_before_client(tmp_path):
    scope = _active_scope(confirmed=False)
    db = _setup(tmp_path, scope)
    client = StubHttpClient(_observed())
    result = await run_http_observe(db, client, task_id="t", scope=scope,
                                    url="https://www.example.com/")
    assert result.status == "blocked"
    assert result.error == "scope_not_confirmed"
    assert client.calls == []
