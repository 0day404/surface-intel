from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from . import __version__


class FofaClientError(RuntimeError):
    pass


class FofaNotConfiguredError(FofaClientError):
    pass


class JsonRpcTransport(Protocol):
    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None: ...


def unwrap_mcp_result(response: dict[str, Any]) -> dict[str, Any]:
    if "error" in response:
        error = response["error"]
        raise FofaClientError(str(error.get("message", error)))
    result = response.get("result", response)
    if not isinstance(result, dict):
        raise FofaClientError("invalid_mcp_result")
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                try:
                    value = json.loads(item.get("text", ""))
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    return result


class StdioJsonRpcTransport:
    def __init__(
        self,
        command: tuple[str, ...],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 30.0,
    ):
        self.command = command
        self.cwd = str(cwd) if cwd else None
        self.env = env
        self.timeout = timeout
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self.process is not None:
            return
        if not self.command:
            raise FofaNotConfiguredError("fofa_command_not_configured")
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=self.cwd,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.start()
        assert self.process and self.process.stdin and self.process.stdout
        async with self._lock:
            self._next_id += 1
            request_id = self._next_id
            message = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                message["params"] = params
            self.process.stdin.write((json.dumps(message, ensure_ascii=True) + "\n").encode())
            await self.process.stdin.drain()
            while True:
                line = await asyncio.wait_for(self.process.stdout.readline(), self.timeout)
                if not line:
                    raise FofaClientError("fofa_mcp_process_closed")
                value = json.loads(line.decode("utf-8"))
                if value.get("id") == request_id:
                    return value

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self.start()
        assert self.process and self.process.stdin
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.process.stdin.write((json.dumps(message, ensure_ascii=True) + "\n").encode())
        await self.process.stdin.drain()

    async def close(self) -> None:
        if getattr(self, "process", None) is None:
            return
        process = self.process
        self.process = None
        if process.stdin is not None:
            process.stdin.close()
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.communicate(), self.timeout)
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()

    async def __aenter__(self) -> "StdioJsonRpcTransport":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


@dataclass
class FofaStdioClient:
    transport: JsonRpcTransport
    protocol_version: str = "2025-03-26"
    initialized: bool = False

    async def initialize(self) -> dict[str, Any]:
        response = await self.transport.request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "surface-intel", "version": __version__},
            },
        )
        result = unwrap_mcp_result(response)
        await self.transport.notify("notifications/initialized", {})
        self.initialized = True
        return result

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.initialized:
            await self.initialize()
        response = await self.transport.request("tools/call", {"name": name, "arguments": arguments})
        return unwrap_mcp_result(response)

    async def __aenter__(self) -> "FofaStdioClient":
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if close is not None:
            await close()


def configured_fofa_client(
    *,
    project_root: str | Path = r"E:\Work\Copilot\fofa-mcp",
    timeout: float = 30.0,
) -> FofaStdioClient:
    command_value = os.getenv("SURFACE_INTEL_FOFA_COMMAND", "").strip()
    command = tuple(command_value.split()) if command_value else ("uv", "run", "fofa-mcp")
    cwd = os.getenv("SURFACE_INTEL_FOFA_ROOT", str(project_root))
    return FofaStdioClient(StdioJsonRpcTransport(command, cwd=cwd, timeout=timeout))
