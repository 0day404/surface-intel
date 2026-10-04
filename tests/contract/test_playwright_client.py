"""R6.3 Playwright MCP client: stdio wiring, network redaction, third-party
detection and explicit not_configured reasons - transport fully injected."""
import json

import pytest

import surface_intel
from surface_intel.playwright_client import PlaywrightMcpClient


class FakeMcpTransport:
    def __init__(self, responses=None, exc=None, tools=None):
        self.responses = responses or {}
        self.exc = exc
        self.calls = []
        self.tools = tools if tools is not None else [
            {"name": "browser_navigate"},
            {"name": "browser_console_messages"},
            {"name": "browser_network_requests"},
            {"name": "browser_take_screenshot"},
        ]

    async def request(self, method, params=None):
        self.calls.append((method, params))
        if self.exc is not None:
            raise self.exc
        if method == "initialize":
            return {"result": {"serverInfo": {"name": "playwright"}}}
        if method == "tools/list":
            return {"result": {"tools": self.tools}}
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
    # handshake happened exactly once, then tools/list probe, then tool calls
    handshake = [c for c in transport.calls if c[0] == "initialize"]
    assert len(handshake) == 1
    init_params = handshake[0][1]
    assert init_params["clientInfo"]["version"] == surface_intel.__version__
    assert [c[0] for c in transport.calls[:2]] == ["initialize", "tools/list"]
    tool_calls = [c for c in transport.calls if c[0] == "tools/call"]
    # health 判活走 tools/list，不再调用不存在的 "health" 工具（D4）
    assert [c[1]["name"] for c in tool_calls] == [
        "browser_navigate", "browser_console_messages",
        "browser_network_requests", "browser_take_screenshot",
    ]


@pytest.mark.asyncio
async def test_health_without_browser_tools_reports_missing():
    transport = FakeMcpTransport(tools=[{"name": "some_other_tool"}])
    client = PlaywrightMcpClient(transport=transport)
    health = await client.health()
    assert health["ok"] is False
    assert health["status"] == "not_configured"
    assert health["error"] == "browser_tools_missing"


@pytest.mark.asyncio
async def test_health_tools_list_failure_maps_to_mcp_error():
    class _ToolsListFails(FakeMcpTransport):
        async def request(self, method, params=None):
            if method == "tools/list":
                raise RuntimeError("boom")
            return await super().request(method, params)

    client = PlaywrightMcpClient(transport=_ToolsListFails())
    health = await client.health()
    assert health["ok"] is False
    assert health["error"] == "mcp_error:RuntimeError"


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


@pytest.mark.asyncio
async def test_partial_collection_errors_are_recorded_not_swallowed():
    transport = FakeMcpTransport({
        "browser_navigate": {"url": "https://www.example.com/", "title": "Example",
                             "status_code": 200},
        "browser_console_messages": RuntimeError("console boom"),
        "browser_network_requests": RuntimeError("network boom"),
        "browser_take_screenshot": RuntimeError("shot boom"),
    })
    client = PlaywrightMcpClient(transport=transport)
    outcome = await client.observe_public_page("https://www.example.com/")
    # 主体观察仍然成立，但三个部分失败必须留痕（D6：不再静默吞）
    assert outcome["status"] == "observed"
    assert outcome["partial_collection_errors"] == [
        "console:RuntimeError", "network:RuntimeError", "screenshot:RuntimeError",
    ]
    assert outcome["console_error_count"] == 0
    assert outcome["network_summary"] == []
    assert outcome["screenshot_ref"] is None


@pytest.mark.asyncio
async def test_partial_collection_errors_empty_on_full_success():
    transport = FakeMcpTransport({
        "browser_navigate": {"url": "https://www.example.com/", "status_code": 200},
        "browser_console_messages": {"messages": []},
        "browser_network_requests": {"requests": []},
        "browser_take_screenshot": {"filename": "obs-x"},
    })
    client = PlaywrightMcpClient(transport=transport)
    outcome = await client.observe_public_page("https://www.example.com/")
    assert outcome["partial_collection_errors"] == []


@pytest.mark.asyncio
async def test_screenshot_relative_ref_is_resolved_against_cwd(tmp_path):
    transport = FakeMcpTransport({
        "browser_navigate": {"url": "https://www.example.com/", "status_code": 200},
        "browser_take_screenshot": {"filename": "observation-x.png"},
    })
    client = PlaywrightMcpClient(transport=transport, cwd=tmp_path)
    outcome = await client.observe_public_page("https://www.example.com/")
    shot = [c for c in transport.calls
            if c[0] == "tools/call" and c[1]["name"] == "browser_take_screenshot"][0]
    # filename 带时间戳，多次观察不再互相覆盖（D7）
    assert shot[1]["arguments"]["filename"].startswith("observation-2")
    assert outcome["screenshot_ref"] == str(tmp_path / "observation-x.png")


@pytest.mark.asyncio
async def test_screenshot_absolute_ref_passes_through(tmp_path):
    absolute = str(tmp_path / "abs.png")
    transport = FakeMcpTransport({
        "browser_navigate": {"url": "https://www.example.com/", "status_code": 200},
        "browser_take_screenshot": {"filename": absolute},
    })
    client = PlaywrightMcpClient(transport=transport, cwd=tmp_path)
    outcome = await client.observe_public_page("https://www.example.com/")
    assert outcome["screenshot_ref"] == absolute


def test_configured_client_passes_cwd_through(monkeypatch, tmp_path):
    monkeypatch.delenv("SURFACE_INTEL_PLAYWRIGHT_COMMAND", raising=False)
    from surface_intel.playwright_client import configured_playwright_client

    client = configured_playwright_client(cwd=tmp_path)
    assert client._cwd == str(tmp_path)
