from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from .base import AdapterManifest, AdapterResult, TaskContext


class McpCallClient(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class InjectedMcpCallClient:
    """Transport-neutral client for tests or a host-provided MCP session."""

    def __init__(self, call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]):
        self._call = call

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self._call(name, arguments)


class FofaMcpAdapter:
    """Thin adapter; the MCP client is injected and never copied into this project."""

    manifest = AdapterManifest(
        "fofa_mcp", "external", (
            "fofa_health", "fofa_account_info", "fofa_search",
            "fofa_search_structured", "fofa_search_next", "fofa_stats", "fofa_host",
        ),
        supports_pagination=True, supports_resume=True, cost_units=1,
    )

    def __init__(self, call_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | McpCallClient):
        self.client = call_tool if hasattr(call_tool, "call_tool") else InjectedMcpCallClient(call_tool)

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await self.client.call_tool(tool, arguments)

    async def health(self, context: TaskContext) -> AdapterResult:
        try:
            value = await self._call("fofa_health", {})
        except (LookupError, RuntimeError) as exc:
            return AdapterResult("unknown", error=f"not_configured:{type(exc).__name__}")
        return AdapterResult(
            "completed" if value.get("ok", False) else "failed",
            request_id=value.get("request_id"),
            error=value.get("error"),
        )

    async def validate(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        return AdapterResult("completed" if action.get("query") else "blocked", error=None if action.get("query") else "query_required")

    async def execute(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        tool = str(action.get("tool") or ("fofa_search_next" if action.get("next_token") else "fofa_search"))
        payload = await self._call(tool, action)
        error = payload.get("error")
        if isinstance(error, dict):
            error = error.get("message") or error.get("type") or str(error)
        if payload.get("error") and (
            payload.get("status") in {"failed", "error"} or payload.get("ok") is False
        ):
            return AdapterResult(
                "failed",
                request_id=payload.get("request_id"),
                error=str(error),
            )
        observations = payload.get("observations", payload.get("results", payload.get("data", [])))
        pagination = payload.get("pagination") or {}
        collection = payload.get("collection") or {}
        cost = payload.get("cost") or {}
        status = payload.get("status")
        if status is None:
            status = "completed" if payload.get("ok") is True else "failed"
        if tool in {"fofa_health", "fofa_account_info", "fofa_stats", "fofa_host"} and not observations:
            observations = [payload]
        return AdapterResult(
            status,
            observations=observations,
            next_token=payload.get("next_token") or pagination.get("next_token"),
            has_more=bool(payload.get("has_more", collection.get("has_more"))),
            cost_units=int(payload.get("cost_units", 0)),
            consumed_fpoints=cost.get("consumed_fpoint"),
            required_fpoints=cost.get("required_fpoints"),
            request_id=payload.get("request_id"),
            error=str(error) if error else None,
            evidence_refs=[str(payload.get("consumed_fpoint"))] if payload.get("consumed_fpoint") is not None else [],
        )

    async def cancel(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        return AdapterResult("cancelled", error="fofa_cancellation_not_supported")
