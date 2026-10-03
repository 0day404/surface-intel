"""R6.3 Playwright runner: gates, browser scope object, redaction, review items."""
import json

import pytest

from surface_intel.policy.scope import ActiveLevel, Scope
from surface_intel.playwright_runner import run_browser_observe
from surface_intel.state.database import Database


class StubBrowserClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def observe_public_page(self, url, *, scope_domains=(), max_requests=100):
        self.calls.append((url, tuple(scope_domains), max_requests))
        return dict(self.outcome)

    async def health(self):
        return {"ok": True}


def _observed(**overrides):
    outcome = {
        "status": "observed",
        "page_url": "https://www.example.com/",
        "final_url": "https://www.example.com/",
        "title": "Example",
        "http_status": 200,
        "network_summary": [{"url": "https://www.example.com/app.js", "status": 200}],
        "third_party_origins": [],
        "console_error_count": 0,
        "screenshot_ref": "obs-1",
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
async def test_happy_path_browser_observation_persisted(tmp_path):
    scope = _active_scope()
    db = _setup(tmp_path, scope)
    client = StubBrowserClient(_observed())
    result = await run_browser_observe(db, client, task_id="t", scope=scope,
                                       url="https://www.example.com/")
    assert result.status == "completed"
    payload = _payload(db)[0]
    assert payload["source"] == "playwright_mcp"
    assert payload["final_url"] == "https://www.example.com/"
    assert payload["related_scope_status"] == "candidate"
    # the browser scope object passes the confirmed domains down
    assert ("https://www.example.com/", ("example.com", "*.example.com"), 100) in client.calls


@pytest.mark.asyncio
async def test_third_party_origins_escalate_to_needs_review(tmp_path):
    scope = _active_scope()
    db = _setup(tmp_path, scope)
    client = StubBrowserClient(_observed(third_party_origins=["cdn.thirdparty.net"]))
    result = await run_browser_observe(db, client, task_id="t", scope=scope,
                                       url="https://www.example.com/")
    assert result.status == "completed"
    assert _payload(db)[0]["related_scope_status"] == "needs_review"


@pytest.mark.asyncio
async def test_gates_block_before_any_observation(tmp_path):
    unconfirmed = _active_scope(confirmed=False)
    db = _setup(tmp_path, unconfirmed)
    client = StubBrowserClient(_observed())
    result = await run_browser_observe(db, client, task_id="t", scope=unconfirmed,
                                       url="https://www.example.com/")
    assert result.error == "scope_not_confirmed"

    passive = _active_scope(active_testing_allowed=False,
                            active_testing_level=ActiveLevel.PASSIVE_ONLY)
    db2 = _setup(tmp_path / "p", passive)
    client2 = StubBrowserClient(_observed())
    result2 = await run_browser_observe(db2, client2, task_id="t", scope=passive,
                                        url="https://www.example.com/")
    assert result2.error == "active_testing_not_authorized"

    out_of_scope = _active_scope(authorized_domains=("other.net",))
    db3 = _setup(tmp_path / "o", out_of_scope)
    client3 = StubBrowserClient(_observed())
    result3 = await run_browser_observe(db3, client3, task_id="t", scope=out_of_scope,
                                        url="https://www.example.com/")
    assert result3.error == "target_out_of_scope"

    for stored_client in (client, client2, client3):
        assert stored_client.calls == []


@pytest.mark.asyncio
async def test_architecture_gate_blocks_browser_probing(tmp_path):
    scope = _active_scope()
    db = _setup(tmp_path, scope, assets=(("domain", "www.example.com", "waf_likely"),))
    client = StubBrowserClient(_observed())
    result = await run_browser_observe(db, client, task_id="t", scope=scope,
                                       url="https://www.example.com/")
    assert result.status == "blocked"
    assert result.error == "architecture_blocked:waf_likely"
    assert result.review_created is True
    assert client.calls == []


@pytest.mark.asyncio
async def test_state_change_and_login_actions_refused(tmp_path):
    """The adapter contract itself refuses login/state-change/browser downloads."""
    from surface_intel.adapters.base import TaskContext
    from surface_intel.adapters.playwright import PlaywrightAdapter

    adapter = PlaywrightAdapter(None)
    context = TaskContext("t", "v", {"scope_confirmed": True, "active_level": "safe_discovery",
                                     "allowed_domains": ["example.com"]})
    for flag in ("allow_login", "allow_state_change", "allow_download"):
        result = await adapter.validate({"target": "https://www.example.com/", flag: True}, context)
        assert result.status == "blocked"
