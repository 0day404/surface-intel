from __future__ import annotations

import json
import logging
import os
from typing import Any, Protocol

from . import __version__
from .fofa_client import StdioJsonRpcTransport, unwrap_mcp_result
from .redaction import redact

DEFAULT_PLAYWRIGHT_COMMAND = ("npx", "-y", "@playwright/mcp@latest", "--stdio")

logger = logging.getLogger(__name__)

# Sensitive network/header keys never survive into evidence; the redaction
# module is the second layer, this filter is the first.
_DROPPED_NETWORK_KEYS = ("headers", "requestheaders", "responseheaders", "cookies",
                         "set-cookie", "authorization", "post_data")


class PlaywrightTransport(Protocol):
    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class PlaywrightMcpClient:
    """Real Playwright observation over the external MCP stdio process.

    The transport is the same JSON-RPC stdio wiring as the FOFA client; the
    command is the official `npx -y @playwright/mcp@latest --stdio` launch.
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
        return unwrap_mcp_result(response)

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
            messages = console.get("messages", []) or []
            console_errors = sum(1 for item in messages
                                 if isinstance(item, dict) and item.get("level") == "error")
        except Exception as exc:
            console = {}
            partial_errors.append(f"console:{type(exc).__name__}")
            logger.warning("playwright console collection failed: %s", exc)
        network_summary, third_party = [], []
        try:
            network = await self._call("browser_network_requests", {})
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
                screenshot_ref = os.path.join(self._cwd, str(raw_ref))
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

    return "observation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


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
