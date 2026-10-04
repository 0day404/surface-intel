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
        target = str(action.get("lookup_target", "")).strip() or str(action.get("target", ""))
        parsed = urlsplit(target)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return AdapterResult("blocked", error="http_target_required")
        if not context.scope_confirmed:
            return AdapterResult("blocked", error="scope_not_confirmed")
        if context.active_level not in {"safe_discovery", "safe_probe"}:
            return AdapterResult("blocked", error="active_level_insufficient")
        method = str(action.get("method", "GET")).upper()
        allowed_methods = {str(item).upper() for item in context.metadata.get("allowed_methods", ("GET",))}
        if action.get("allow_state_change", False) or method not in allowed_methods:
            return AdapterResult("blocked", error="method_not_allowed")
        if self.transport is None:
            return AdapterResult("blocked", error="transport_not_configured")
        return AdapterResult("completed")

    async def execute(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        validation = await self.validate(action, context)
        if validation.status != "completed":
            return validation
        anchor = str(action["target"])
        target = str(action.get("lookup_target", "")).strip() or anchor
        response = await self.transport(target)
        transport_status = str(
            response.get("transport_status", response.get("status", "observed"))).lower()
        observation = {
            "target": target,
            "scope_anchor": anchor if target != anchor else None,
            "protocol": urlsplit(target).scheme,
            "method": str(action.get("method", "GET")).upper(),
            "status_code": response.get("http_status", response.get("status_code")),
            "status_text": response.get("status_text", ""),
            "response_class": response.get("response_class", response.get("application_status", "http_response")),
            "final_url": response.get("final_url", target),
            "redirect_chain": list(response.get("redirect_chain", [])),
            "headers": dict(response.get("headers", {})),
            "content_type": response.get("content_type"),
            "body_sha256": response.get("body_sha256"),
            "body_bytes": int(response.get("body_bytes", response.get("response_size", 0))),
            "body_truncated": bool(response.get("body_truncated", False)),
            "tls_sha256": response.get("tls_sha256"),
            "response_time_ms": response.get("response_ms", response.get("response_time_ms")),
            "transport_status": transport_status,
            "error": response.get("error"),
            "error_type": response.get("error_type"),
            "application_status": response.get("application_status", "http_response"),
            "source": self.manifest.name,
        }
        if transport_status == "timeout":
            return AdapterResult("timed_out", observations=[observation], error="timeout")
        if transport_status not in {"observed", "open", "http_response"}:
            return AdapterResult("failed", observations=[observation], error=transport_status)
        return AdapterResult("completed", observations=[observation], cost_units=self.manifest.cost_units)

    async def cancel(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        return AdapterResult("cancelled")
