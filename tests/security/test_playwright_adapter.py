import pytest

from surface_intel.adapters.base import TaskContext
from surface_intel.adapters.playwright import PlaywrightAdapter


@pytest.mark.asyncio
async def test_playwright_blocks_third_party_and_state_change():
    async def call_tool(name, payload):
        return {"status": "completed", "status_code": 200, "page_url": payload["url"]}

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
