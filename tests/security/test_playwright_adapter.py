import pytest

from surface_intel.adapters.base import TaskContext
from surface_intel.adapters.playwright import PlaywrightAdapter


@pytest.mark.asyncio
async def test_playwright_blocks_third_party_and_state_change():
    async def call_tool(name, payload):
        return {"status": "observed", "http_status": 200,
                "network_summary": [], "page_url": payload["url"]}

    adapter = PlaywrightAdapter(call_tool)
    context = TaskContext("t", "s", {
        "scope_confirmed": True, "active_level": "safe_discovery",
        "allowed_domains": ["example.com"],
    })
    assert (await adapter.execute({"target": "https://third-party.test"}, context)).status == "blocked"
    assert (await adapter.execute({"target": "https://example.com", "allow_state_change": True}, context)).status == "blocked"
    result = await adapter.execute({"target": "https://www.example.com"}, context)
    assert result.status == "completed"
    assert result.observations[0]["status_code"] == 200


@pytest.mark.asyncio
async def test_playwright_not_configured_is_explicit():
    result = await PlaywrightAdapter().health(TaskContext("t", "s"))
    assert result.status == "unknown"
    assert result.error == "not_configured"


@pytest.mark.asyncio
async def test_scope_payload_judges_host_like_runner():
    """D8：metadata 带 scope_payload 时用 Scope.is_in_scope 判定，与 runner 同一实现。"""
    from surface_intel.policy.scope import ActiveLevel, Scope

    async def call_tool(name, payload):
        return {"status": "observed", "http_status": 200,
                "network_summary": [{"url": payload["url"]}]}

    wildcard = Scope(
        authorized_domains=("*",), confirmed=True, scope_version="s",
        active_testing_allowed=True, active_testing_level=ActiveLevel.SAFE_PROBE,
    )
    context = TaskContext("t", "v", {
        "scope_confirmed": True, "active_level": "safe_probe",
        "scope_payload": wildcard.to_dict(),
    })
    adapter = PlaywrightAdapter(call_tool)
    # 全通配下任意 host 放行（旧的 allowed_domains 后缀匹配会误拦 "*"）
    result = await adapter.execute({"target": "https://anything.test"}, context)
    assert result.status == "completed"
    assert result.observations[0]["status_code"] == 200
    assert result.observations[0]["network_summary"] == [
        {"url": "https://anything.test"}]

    narrow = Scope(
        authorized_domains=("example.com",), confirmed=True, scope_version="s",
        active_testing_allowed=True, active_testing_level=ActiveLevel.SAFE_PROBE,
    )
    context_narrow = TaskContext("t", "v", {
        "scope_confirmed": True, "active_level": "safe_probe",
        "scope_payload": narrow.to_dict(),
    })
    blocked = await adapter.execute({"target": "https://third-party.test"}, context_narrow)
    assert blocked.status == "blocked"
    assert blocked.error == "browser_domain_not_authorized"


@pytest.mark.asyncio
async def test_targets_only_scope_payload_authorizes_exact_target():
    from surface_intel.policy.scope import ActiveLevel, Scope

    async def call_tool(name, payload):
        return {"status": "observed", "http_status": 200, "network_summary": []}

    scope = Scope(
        authorized_targets=("www.example.com",), confirmed=True, scope_version="s",
        active_testing_allowed=True, active_testing_level=ActiveLevel.SAFE_PROBE,
    )
    context = TaskContext("t", "v", {
        "scope_confirmed": True, "active_level": "safe_probe",
        "scope_payload": scope.to_dict(),
    })
    result = await PlaywrightAdapter(call_tool).execute(
        {"target": "https://www.example.com/"}, context)
    assert result.status == "completed"
