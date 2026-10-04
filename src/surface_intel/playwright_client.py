from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Protocol

from . import __version__
from .fofa_client import FofaClientError, StdioJsonRpcTransport, unwrap_mcp_result
from .redaction import redact

# @playwright/mcp 0.0.83+ 移除了 --stdio 旗标（stdio 本就是默认传输，传了会直接退出）
DEFAULT_PLAYWRIGHT_COMMAND = ("npx", "-y", "@playwright/mcp@latest")

logger = logging.getLogger(__name__)

# Sensitive network/header keys never survive into evidence; the redaction
# module is the second layer, this filter is the first.
_DROPPED_NETWORK_KEYS = ("headers", "requestheaders", "responseheaders", "cookies",
                         "set-cookie", "authorization", "post_data")

# @playwright/mcp 0.0.83 的工具结果是人类可读 Markdown 而非 JSON，且无 JSON
# 输出开关（--help 已核实）。以下解析器按 2026-10 实测形状抽取，键名对齐
# 旧 JSON 形状，让 observe_public_page 的消费逻辑保持单一。
#   navigate  → "### Page" 段的 "- Page URL:" / "- Page Title:"
#   console   → "Total messages: N (Errors: E, Warnings: W)" 头行（不逐条列消息）
#   network   → "1. [GET] https://… => [200]" 编号行（需 static 选项才显示）
#   screenshot→ "### Result" 段的首个 markdown 链接即落盘路径
_PAGE_URL_RE = re.compile(r"^- Page URL:\s*(.+)$", re.MULTILINE)
_PAGE_TITLE_RE = re.compile(r"^- Page Title:\s*(.*)$", re.MULTILINE)
_CONSOLE_ERRORS_RE = re.compile(r"Total messages:.*?\(Errors:\s*(\d+)", re.DOTALL)
_NETWORK_LINE_RE = re.compile(
    r"^\s*\d+\.\s*\[(?P<method>\w+)\]\s+(?P<url>\S+)\s*=>\s*\[(?P<status>\d+)\]",
    re.MULTILINE)
_SCREENSHOT_LINK_RE = re.compile(r"\]\(([^)]+)\)")


def _markdown_text(unwrapped: dict[str, Any]) -> str:
    parts = []
    for item in unwrapped.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return "\n".join(parts)


def _parse_navigate(text: str) -> dict[str, Any] | None:
    url_match = _PAGE_URL_RE.search(text)
    if not url_match:
        return None
    parsed: dict[str, Any] = {"url": url_match.group(1).strip()}
    title_match = _PAGE_TITLE_RE.search(text)
    if title_match:
        parsed["title"] = title_match.group(1).strip()
    return parsed


def _parse_console(text: str) -> dict[str, Any] | None:
    if "Total messages" not in text:
        return None
    count_match = _CONSOLE_ERRORS_RE.search(text)
    return {"error_count": int(count_match.group(1)) if count_match else 0}


def _parse_network(text: str) -> dict[str, Any] | None:
    if "### Result" not in text:
        return None
    # 编号行只含 method/url/status，无头无体，天然满足脱敏要求
    requests = [
        {"url": match.group("url"), "method": match.group("method"),
         "status": int(match.group("status"))}
        for match in _NETWORK_LINE_RE.finditer(text)
    ]
    return {"requests": requests}


def _parse_screenshot(text: str) -> dict[str, Any] | None:
    link_match = _SCREENSHOT_LINK_RE.search(text)
    if not link_match:
        return None
    return {"filename": link_match.group(1).strip()}


_PARSE_MARKDOWN = {
    "browser_navigate": _parse_navigate,
    "browser_console_messages": _parse_console,
    "browser_network_requests": _parse_network,
    "browser_take_screenshot": _parse_screenshot,
}


def _normalize_tool_result(tool: str, unwrapped: dict[str, Any]) -> dict[str, Any]:
    """把 0.0.83 的 Markdown 工具结果解析成旧 JSON 形状的 dict。

    unwrap_mcp_result 在 content 文本不是 JSON 时回退返回整个 result 信封
    （带 "content" 键）——按工具解析；已是结构化 dict（旧版本/测试替身/
    structuredContent）则原样放行。isError 信封与无法解析的文本都以
    FofaClientError 显式暴露，绝不静默吞掉。
    """
    if "content" not in unwrapped:
        return unwrapped
    text = _markdown_text(unwrapped)
    if unwrapped.get("isError"):
        raise FofaClientError(f"playwright_tool_error:{text[:160]}")
    parser = _PARSE_MARKDOWN.get(tool)
    parsed = parser(text) if parser else None
    if parsed is None:
        logger.warning("playwright %s response could not be parsed: %.200s", tool, text)
        raise FofaClientError("unparsed_response")
    return parsed


class PlaywrightTransport(Protocol):
    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class PlaywrightMcpClient:
    """Real Playwright observation over the external MCP stdio process.

    The transport is the same JSON-RPC stdio wiring as the FOFA client; the
    command is the official `npx -y @playwright/mcp@latest` launch (stdio is
    its default transport). Tool responses are human-readable Markdown since
    0.0.83 (no JSON mode exists); _normalize_tool_result parses them back
    into structured dicts while passing legacy JSON shapes through.
    No Playwright source, cache, cookies or credentials ever enter this
    project. Public-page observation collects final URL, title, status,
    network summary (URL/method/status/resource-type only - headers and
    bodies are dropped), console error count and screenshot metadata.
    `scope_domains` limits where navigation may land; anything else is
    recorded as a third-party origin, never followed further. Screenshots
    land in `cwd` (the task's screenshots directory); partial collection
    failures are recorded in `partial_collection_errors`, never swallowed.
    """

    source_name = "playwright_mcp"
    launch_command = " ".join(DEFAULT_PLAYWRIGHT_COMMAND)

    def __init__(self, transport: PlaywrightTransport | None = None,
                 command: tuple[str, ...] | None = None, timeout: float = 60.0,
                 cwd: str | os.PathLike | None = None):
        self._command = command or DEFAULT_PLAYWRIGHT_COMMAND
        self._transport = transport
        self._timeout = timeout
        self._cwd = str(cwd) if cwd else None
        self._initialized = False

    def _transport_instance(self) -> PlaywrightTransport:
        if self._transport is None:
            self._transport = StdioJsonRpcTransport(self._command, cwd=self._cwd,
                                                    timeout=self._timeout)
        return self._transport

    async def _ensure_initialized(self, transport: PlaywrightTransport) -> None:
        if not self._initialized:
            await transport.request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "surface-intel", "version": __version__},
            })
            self._initialized = True

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        transport = self._transport_instance()
        await self._ensure_initialized(transport)
        response = await transport.request("tools/call", {"name": tool, "arguments": arguments})
        return _normalize_tool_result(tool, unwrap_mcp_result(response))

    async def close(self) -> None:
        if self._transport is not None:
            await self._transport.close()
            self._transport = None
            self._initialized = False

    async def health(self) -> dict[str, Any]:
        """Probe liveness via initialize + tools/list.

        @playwright/mcp has no "health" tool; calling one was silently mapped
        to mcp_error and always reported not_configured. Instead we complete
        the handshake and require at least one `browser_*` tool to be exposed.
        """
        try:
            transport = self._transport_instance()
            await self._ensure_initialized(transport)
            response = await transport.request("tools/list", {})
            tools = (response.get("result") or {}).get("tools") or []
            names = [str(item.get("name", "")) for item in tools if isinstance(item, dict)]
            if not any(name.startswith("browser_") for name in names):
                return {"ok": False, "status": "not_configured",
                        "error": "browser_tools_missing",
                        "launch_command": self.launch_command}
        except FileNotFoundError:
            return {"ok": False, "status": "not_configured",
                    "error": "node_npx_not_available",
                    "launch_command": self.launch_command}
        except OSError as exc:
            return {"ok": False, "status": "not_configured",
                    "error": f"launch_failed:{type(exc).__name__}",
                    "launch_command": self.launch_command}
        except Exception as exc:
            return {"ok": False, "status": "not_configured",
                    "error": f"mcp_error:{type(exc).__name__}",
                    "launch_command": self.launch_command}
        return {"ok": True, "status": "available", "launch_command": self.launch_command}

    async def observe_public_page(
        self,
        url: str,
        *,
        scope_domains: tuple[str, ...] = (),
        max_requests: int = 100,
    ) -> dict[str, Any]:
        partial_errors: list[str] = []
        try:
            navigation = await self._call("browser_navigate", {"url": url})
        except FileNotFoundError:
            return {"status": "not_configured", "error_type": "node_npx_not_available"}
        except OSError as exc:
            return {"status": "not_configured", "error_type": f"launch_failed:{type(exc).__name__}"}
        except Exception as exc:
            return {"status": "failed", "error_type": f"mcp_error:{type(exc).__name__}"}

        final_url = str(navigation.get("url", url))
        console_errors = 0
        try:
            console = await self._call("browser_console_messages", {})
            if "error_count" in console:
                # 0.0.83 只回计数头行，不逐条列消息——如实取计数
                console_errors = int(console["error_count"])
            else:
                messages = console.get("messages", []) or []
                console_errors = sum(1 for item in messages
                                     if isinstance(item, dict) and item.get("level") == "error")
        except Exception as exc:
            console = {}
            partial_errors.append(f"console:{type(exc).__name__}")
            logger.warning("playwright console collection failed: %s", exc)
        network_summary, third_party = [], []
        try:
            # static: true —— 0.0.83 默认隐藏静态请求（连文档请求都不显示）
            network = await self._call("browser_network_requests", {"static": True})
            entries = network.get("requests", network.get("networkRequests", [])) or []
            for entry in entries[:max_requests]:
                if not isinstance(entry, dict):
                    continue
                cleaned = {
                    key: value for key, value in entry.items()
                    if str(key).lower() not in _DROPPED_NETWORK_KEYS
                }
                network_summary.append(cleaned)
                entry_url = str(entry.get("url", ""))
                host = _host_of(entry_url)
                if host and scope_domains and not _host_allowed(host, scope_domains):
                    third_party.append(host)
        except Exception as exc:
            network_summary = []
            partial_errors.append(f"network:{type(exc).__name__}")
            logger.warning("playwright network collection failed: %s", exc)

        screenshot_ref = None
        try:
            screenshot = await self._call(
                "browser_take_screenshot", {"filename": _screenshot_name()})
            raw_ref = screenshot.get("filename") or screenshot.get("path")
            if raw_ref and self._cwd and not os.path.isabs(str(raw_ref)):
                # 0.0.83 的链接目标是相对 cwd 的（./observation-x.png 或
                # .playwright-mcp\page-x.png），normpath 抹掉 ./ 与多余分隔
                screenshot_ref = os.path.normpath(os.path.join(self._cwd, str(raw_ref)))
            else:
                screenshot_ref = raw_ref
        except Exception as exc:
            screenshot_ref = None
            partial_errors.append(f"screenshot:{type(exc).__name__}")
            logger.warning("playwright screenshot failed: %s", exc)

        payload = {
            "status": "observed",
            "page_url": url,
            "final_url": final_url,
            "title": navigation.get("title"),
            # 0.0.83 的 navigate 结果不暴露 HTTP 状态码——如实置 None，
            # 不从 network_summary 猜测拼凑
            "http_status": navigation.get("status_code") or navigation.get("status"),
            "network_summary": redact(network_summary),
            "third_party_origins": sorted(set(third_party)),
            "console_error_count": console_errors,
            "partial_collection_errors": partial_errors,
            "screenshot_ref": screenshot_ref,
            "queried_at": _now(),
        }
        return payload


def _screenshot_name() -> str:
    from datetime import datetime, timezone

    # 0.0.83 按字面使用 filename（不补扩展名），必须自带 .png
    return "observation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".png"


def _host_of(url: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(url).hostname or "").strip().lower()


def _host_allowed(host: str, domains: tuple[str, ...]) -> bool:
    for entry in domains:
        entry = entry.strip().lower().lstrip("*.").rstrip(".")
        if host == entry or host.endswith("." + entry):
            return True
    return False


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def configured_playwright_client(timeout: float = 60.0,
                                 cwd: str | os.PathLike | None = None) -> PlaywrightMcpClient:
    command_value = os.getenv("SURFACE_INTEL_PLAYWRIGHT_COMMAND", "").strip()
    command = tuple(command_value.split()) if command_value else DEFAULT_PLAYWRIGHT_COMMAND
    return PlaywrightMcpClient(command=command, timeout=timeout, cwd=cwd)
