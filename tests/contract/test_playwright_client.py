"""R6.3 Playwright MCP client: stdio wiring, network redaction, third-party
detection and explicit not_configured reasons - transport fully injected."""
import json

import pytest

from surface_intel.playwright_client import PlaywrightMcpClient


class FakeMcpTransport:
    def __init__(self, responses=None, exc=None):
        self.responses = responses or {}
        self.exc = exc
        self.calls = []

    async def request(self, method, params=None):
        self.calls.append((method, params))
        if self.exc is not None:
            raise self.exc
        if method == "initialize":
            return {"result": {"serverInfo": {"name": "playwright"}}}
        if method == "tools/call":
            name = params.get("name")
            if name in self.responses:
                outcome = self.responses[name]
                if isinstance(outcome, Exception):
                    raise outcome
                return {"result": {"content": [{"type": "text",
                                                "text": json.dumps(outcome)}]}}
            return {"result": {"content": [{"type": "text", "text": "{}"}]}}
        return {"result": {}}

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_handshake_and_observation_mapping_with_redaction():
    transport = FakeMcpTransport({
        "browser_navigate": {"url": "https://www.example.com/login-free",
                             "title": "Example", "status_code": 200},
        "browser_console_messages": {"messages": [
            {"level": "error", "text": "boom"},
            {"level": "info", "text": "loaded"},
        ]},
        "browser_network_requests": {"requests": [
            {"url": "https://www.example.com/api", "method": "GET", "status": 200,
             "resource_type": "xhr", "headers": {"authorization": "secret"}},
            {"url": "https://cdn.thirdparty.net/x.js", "method": "GET", "status": 200,
             "resource_type": "script", "cookies": "abc"},
        ]},
        "browser_take_screenshot": {"filename": "obs-1"},
    })
    client = PlaywrightMcpClient(transport=transport)
    health = await client.health()
    assert health["ok"] is True

    outcome = await client.observe_public_page(
        "https://www.example.com/", scope_domains=("example.com",), max_requests=100)
    assert outcome["status"] == "observed"
    assert outcome["final_url"] == "https://www.example.com/login-free"
    assert outcome["console_error_count"] == 1
    assert outcome["screenshot_ref"] == "obs-1"
    # network summary survives but headers/cookies are dropped before evidence
    assert len(outcome["network_summary"]) == 2
    for entry in outcome["network_summary"]:
        assert "headers" not in entry and "cookies" not in entry
    # third-party origin detected from the network summary
    assert outcome["third_party_origins"] == ["cdn.thirdparty.net"]
    # handshake happened exactly once, then tool calls
    handshake = [c for c in transport.calls if c[0] == "initialize"]
    assert len(handshake) == 1
    tool_calls = [c for c in transport.calls if c[0] == "tools/call"]
    assert [c[1]["name"] for c in tool_calls] == [
        "health", "browser_navigate", "browser_console_messages",
        "browser_network_requests", "browser_take_screenshot",
    ]


@pytest.mark.asyncio
async def test_launch_failure_yields_explicit_not_configured():
    transport = FakeMcpTransport(exc=FileNotFoundError())
    client = PlaywrightMcpClient(transport=transport)
    health = await client.health()
    assert health["ok"] is False
    assert health["status"] == "not_configured"
    assert health["error"] == "node_npx_not_available"
    assert "npx -y @playwright/mcp@latest" in health["launch_command"]

    outcome = await client.observe_public_page("https://www.example.com/")
    assert outcome["status"] == "not_configured"


@pytest.mark.asyncio
async def test_launch_os_error_maps_to_not_configured():
    transport = FakeMcpTransport(exc=OSError("spawn failed"))
    client = PlaywrightMcpClient(transport=transport)
    health = await client.health()
    assert health["ok"] is False
    assert health["error"] == "launch_failed:OSError"


@pytest.mark.asyncio
async def test_max_requests_bounds_the_network_summary():
    transport = FakeMcpTransport({
        "browser_network_requests": {"requests": [
            {"url": f"https://www.example.com/{i}", "method": "GET", "status": 200}
            for i in range(50)
        ]},
    })
    client = PlaywrightMcpClient(transport=transport)
    outcome = await client.observe_public_page("https://www.example.com/", max_requests=10)
    assert len(outcome["network_summary"]) == 10


def test_configured_client_uses_default_command(monkeypatch):
    monkeypatch.delenv("SURFACE_INTEL_PLAYWRIGHT_COMMAND", raising=False)
    from surface_intel.playwright_client import configured_playwright_client

    client = configured_playwright_client()
    assert client.launch_command == "npx -y @playwright/mcp@latest --stdio"
