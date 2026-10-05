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


def _windows_spawn_argv(argv: list[str]) -> list[str]:
    """Windows: batch shims (npx.cmd / .bat) cannot be exec'd directly.

    asyncio's create_subprocess_exec goes through CreateProcess, which only
    runs real executables - spawning "npx" raises FileNotFoundError. When the
    PATH-resolved shim is a batch file, prefer driving node against npm's
    npx-cli.js (no shell quoting pitfalls); cmd.exe /d /s /c is the fallback.
    """
    if os.name != "nt":
        return argv
    import shutil

    resolved = shutil.which(argv[0])
    if not resolved or not resolved.lower().endswith((".cmd", ".bat")):
        return argv
    shim_root = os.path.dirname(os.path.dirname(resolved))
    npx_cli = os.path.join(shim_root, "node_modules", "npm", "bin", "npx-cli.js")
    node = shutil.which("node")
    if node and os.path.isfile(npx_cli):
        return [node, npx_cli, *argv[1:]]
    return ["cmd.exe", "/d", "/s", "/c", *argv]


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
        argv = _windows_spawn_argv(list(self.command))
        self.process = await asyncio.create_subprocess_exec(
            *argv,
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
        # Explicit, ordered shutdown: close the pipes and await their transports
        # actually closing (Proactor __del__ would otherwise surface "unclosed
        # transport" as an unraisable the entry-point filterwarnings cannot
        # suppress), then reap with wait() - never communicate(), which races
        # "I/O operation on closed pipe" once stdin is already closed.
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError:
                pass
            waiter = getattr(stream, "wait_closed", None)
            if waiter is not None:
                try:
                    await asyncio.wait_for(waiter(), 2.0)
                except (asyncio.TimeoutError, OSError):
                    pass  # child already gone / pipe state irrelevant at teardown
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), self.timeout)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

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


# fofa-mcp 是外部依赖(独立项目,本工具只经 stdio 调用)。部署位置按
# 三级解析:env SURFACE_INTEL_FOFA_ROOT > 本项目同级目录(迁移部署时把
# fofa-mcp 放在 surface-intel 旁边即零配置) > 历史固定路径(兼容旧机)。
_LEGACY_FOFA_ROOT = Path(r"E:\Work\Copilot\fofa-mcp")


def _default_fofa_root() -> Path:
    env_root = os.getenv("SURFACE_INTEL_FOFA_ROOT", "").strip()
    if env_root:
        return Path(env_root)
    sibling = Path(__file__).resolve().parents[2] / "fofa-mcp"
    if (sibling / "pyproject.toml").is_file():
        return sibling
    return _LEGACY_FOFA_ROOT


def configured_fofa_client(
    *,
    project_root: str | Path | None = None,
    timeout: float = 30.0,
) -> FofaStdioClient:
    command_value = os.getenv("SURFACE_INTEL_FOFA_COMMAND", "").strip()
    command = tuple(command_value.split()) if command_value else ("uv", "run", "fofa-mcp")
    resolved = Path(project_root) if project_root is not None else _default_fofa_root()
    return FofaStdioClient(StdioJsonRpcTransport(command, cwd=resolved, timeout=timeout))
