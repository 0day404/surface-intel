from __future__ import annotations

from typing import Any, Awaitable, Callable

from .base import AdapterManifest, AdapterResult, TaskContext


class FofaMcpAdapter:
    """Thin adapter; the MCP client is injected and never copied into this project."""

    manifest = AdapterManifest(
        "fofa_mcp", "external", ("fofa_health", "fofa_search", "fofa_search_next", "fofa_stats", "fofa_host"),
        supports_pagination=True, supports_resume=True, cost_units=1,
    )

    def __init__(self, call_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]):
        self.call_tool = call_tool

    async def health(self, context: TaskContext) -> AdapterResult:
        value = await self.call_tool("fofa_health", {})
        return AdapterResult("completed" if value.get("ok", False) else "failed", request_id=value.get("request_id"))

    async def validate(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        return AdapterResult("completed" if action.get("query") else "blocked", error=None if action.get("query") else "query_required")

    async def execute(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        tool = "fofa_search_next" if action.get("next_token") else "fofa_search"
        payload = await self.call_tool(tool, action)
        return AdapterResult(
            payload.get("status", "failed"),
            observations=payload.get("observations", payload.get("results", [])),
            next_token=payload.get("next_token"),
            has_more=bool(payload.get("has_more")),
            cost_units=int(payload.get("cost_units", 0)),
            request_id=payload.get("request_id"),
            error=payload.get("error"),
        )

    async def cancel(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        return AdapterResult("cancelled", error="fofa_cancellation_not_supported")
