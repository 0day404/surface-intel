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
    assert client.launch_command == "npx -y @playwright/mcp@latest"


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


# --- @playwright/mcp 0.0.83：工具结果是人类可读 Markdown（实测形状 fixture） ---

NAVIGATE_MD = (
    "### Ran Playwright code\n"
    "```js\n"
    "await page.goto('https://www.example.com/');\n"
    "```\n"
    "### Page\n"
    "- Page URL: https://www.example.com/\n"
    "- Page Title: Example Domain\n"
    "### Snapshot\n"
    "- [Snapshot](.playwright-mcp\\page-1.yml)"
)
CONSOLE_MD = ("### Result\n"
              "Total messages: 2 (Errors: 1, Warnings: 1)\n"
              "\n"
              "### Page\n"
              "- Page URL: https://www.example.com/")
NETWORK_MD = ("### Result\n"
              "1. [GET] https://www.example.com/ => [200] \n"
              "2. [GET] https://cdn.thirdparty.net/x.js => [200]")
SCREENSHOT_MD = ("### Result\n"
                 "- [Screenshot of viewport](./observation-x.png)\n"
                 "### Ran Playwright code")


class MarkdownMcpTransport(FakeMcpTransport):
    """模拟 0.0.83：tools/call 一律返回非 JSON 的 Markdown 文本。"""

    def __init__(self, texts=None, error_names=(), tools=None):
        super().__init__(tools=tools)
        self.texts = texts or {}
        self.error_names = set(error_names)

    async def request(self, method, params=None):
        if method == "tools/call":
            self.calls.append((method, params))
            name = params.get("name")
            if name in self.texts:
                envelope = {"content": [{"type": "text", "text": self.texts[name]}]}
                if name in self.error_names:
                    envelope["isError"] = True
                return {"result": envelope}
            return {"result": {"content": [{"type": "text", "text": "### Result\n"}]}}
        return await super().request(method, params)


@pytest.mark.asyncio
async def test_markdown_responses_are_parsed(tmp_path):
    transport = MarkdownMcpTransport({
        "browser_navigate": NAVIGATE_MD,
        "browser_console_messages": CONSOLE_MD,
        "browser_network_requests": NETWORK_MD,
        "browser_take_screenshot": SCREENSHOT_MD,
    })
    client = PlaywrightMcpClient(transport=transport, cwd=tmp_path)
    outcome = await client.observe_public_page(
        "https://www.example.com/", scope_domains=("example.com",))
    assert outcome["status"] == "observed"
    assert outcome["final_url"] == "https://www.example.com/"
    assert outcome["title"] == "Example Domain"
    assert outcome["console_error_count"] == 1
    # 0.0.83 的 navigate 不暴露 HTTP 状态码——如实为 None，不编造
    assert outcome["http_status"] is None
    # 编号行解析出 method/url/status，第三方 origin 可判定
    assert outcome["network_summary"] == [
        {"url": "https://www.example.com/", "method": "GET", "status": 200},
        {"url": "https://cdn.thirdparty.net/x.js", "method": "GET", "status": 200},
    ]
    assert outcome["third_party_origins"] == ["cdn.thirdparty.net"]
    # 链接目标 ./observation-x.png 相对 cwd 解析
    assert outcome["screenshot_ref"] == str(tmp_path / "observation-x.png")
    assert outcome["partial_collection_errors"] == []


@pytest.mark.asyncio
async def test_network_requests_request_static_option():
    transport = MarkdownMcpTransport({"browser_navigate": NAVIGATE_MD})
    client = PlaywrightMcpClient(transport=transport)
    await client.observe_public_page("https://www.example.com/")
    # 0.0.83 默认隐藏静态请求（连文档请求都不显示），必须带 static
    network_call = [c for c in transport.calls
                    if c[0] == "tools/call" and c[1]["name"] == "browser_network_requests"][0]
    assert network_call[1]["arguments"] == {"static": True}


@pytest.mark.asyncio
async def test_unparsed_markdown_is_recorded_not_swallowed():
    transport = MarkdownMcpTransport({
        "browser_navigate": NAVIGATE_MD,
        "browser_console_messages": "### Result\nsomething unrecognized",
        "browser_network_requests": NETWORK_MD,
        "browser_take_screenshot": SCREENSHOT_MD,
    })
    client = PlaywrightMcpClient(transport=transport)
    outcome = await client.observe_public_page("https://www.example.com/")
    assert outcome["status"] == "observed"
    assert outcome["partial_collection_errors"] == ["console:FofaClientError"]
    assert outcome["console_error_count"] == 0


@pytest.mark.asyncio
async def test_is_error_envelope_fails_navigation():
    transport = MarkdownMcpTransport(
        {"browser_navigate": "### Result\nNavigation failed with TimeoutError"},
        error_names=("browser_navigate",))
    client = PlaywrightMcpClient(transport=transport)
    outcome = await client.observe_public_page("https://www.example.com/")
    assert outcome["status"] == "failed"
    assert outcome["error_type"] == "mcp_error:FofaClientError"


@pytest.mark.asyncio
async def test_is_error_envelope_records_partial_error():
    transport = MarkdownMcpTransport(
        {"browser_navigate": NAVIGATE_MD,
         "browser_console_messages": CONSOLE_MD,
         "browser_network_requests": NETWORK_MD,
         "browser_take_screenshot": "### Result\nScreenshot refused"},
        error_names=("browser_take_screenshot",))
    client = PlaywrightMcpClient(transport=transport)
    outcome = await client.observe_public_page("https://www.example.com/")
    assert outcome["status"] == "observed"
    assert outcome["partial_collection_errors"] == ["screenshot:FofaClientError"]
    assert outcome["screenshot_ref"] is None


@pytest.mark.asyncio
async def test_screenshot_filename_argument_carries_png_extension(tmp_path):
    transport = MarkdownMcpTransport({
        "browser_navigate": NAVIGATE_MD,
        "browser_take_screenshot": SCREENSHOT_MD,
    })
    client = PlaywrightMcpClient(transport=transport, cwd=tmp_path)
    await client.observe_public_page("https://www.example.com/")
    shot_call = [c for c in transport.calls
                 if c[0] == "tools/call" and c[1]["name"] == "browser_take_screenshot"][0]
    # 0.0.83 按字面使用 filename（不补扩展名）
    assert shot_call[1]["arguments"]["filename"].endswith(".png")
    assert shot_call[1]["arguments"]["filename"].startswith("observation-2")
