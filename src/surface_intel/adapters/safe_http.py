from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from .base import AdapterManifest, AdapterResult, TaskContext


class SafeHttpAdapter:
    """Low-impact HTTP observation with an injected transport for deterministic tests."""

    manifest = AdapterManifest(
        "safe_http",
        "1.0",
        ("http_observation", "web_fingerprint"),
        required_active_level="safe_discovery",
        cost_units=1,
        supports_cancellation=False,
        external_side_effects=True,
        retry_policy="single_safe_retry",
    )

    def __init__(self, transport: Callable[[str], Awaitable[dict[str, Any]]] | None = None):
        self.transport = transport

    async def health(self, context: TaskContext) -> AdapterResult:
        return AdapterResult("completed", request_id=f"health-{self.manifest.name}")

    async def validate(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        target = str(action.get("target", ""))
        parsed = urlsplit(target)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return AdapterResult("blocked", error="http_target_required")
        if not context.scope_confirmed:
            return AdapterResult("blocked", error="scope_not_confirmed")
        if context.active_level not in {"safe_discovery", "safe_probe"}:
            return AdapterResult("blocked", error="active_level_insufficient")
        if action.get("allow_state_change", False) or str(action.get("method", "GET")).upper() != "GET":
            return AdapterResult("blocked", error="state_change_not_allowed")
        if self.transport is None:
            return AdapterResult("blocked", error="transport_not_configured")
        return AdapterResult("completed")

    async def execute(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        validation = await self.validate(action, context)
        if validation.status != "completed":
            return validation
        target = str(action["target"])
        response = await self.transport(target)
        observation = {
            "target": target,
            "protocol": urlsplit(target).scheme,
            "method": "GET",
            "status_code": response.get("status_code"),
            "status_text": response.get("status_text", ""),
            "response_size": int(response.get("response_size", 0)),
            "redirect_chain": list(response.get("redirect_chain", [])),
            "response_time_ms": response.get("response_time_ms"),
            "transport_status": response.get("transport_status", "open"),
            "application_status": response.get("application_status", "http_response"),
        }
        return AdapterResult("completed", observations=[observation], cost_units=self.manifest.cost_units)

    async def cancel(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        return AdapterResult("cancelled")
