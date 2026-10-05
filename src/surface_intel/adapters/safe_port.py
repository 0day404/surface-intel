from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .base import AdapterManifest, AdapterResult, TaskContext


class SafePortAdapter:
    """Known-port verification only; the connector is injected and never scans ranges."""

    manifest = AdapterManifest(
        "safe_port",
        "1.0",
        ("known_port_verification", "service_observation"),
        required_active_level="safe_discovery",
        cost_units=1,
        supports_cancellation=False,
        external_side_effects=True,
        retry_policy="none",
    )

    def __init__(self, connector: Callable[[str, int], Awaitable[dict[str, Any]]] | None = None):
        self.connector = connector

    async def health(self, context: TaskContext) -> AdapterResult:
        return AdapterResult("completed", request_id=f"health-{self.manifest.name}")

    async def validate(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        if not context.scope_confirmed:
            return AdapterResult("blocked", error="scope_not_confirmed")
        if context.active_level not in {"safe_discovery", "safe_probe"}:
            return AdapterResult("blocked", error="active_level_insufficient")
        if self.connector is None:
            return AdapterResult("blocked", error="connector_not_configured")
        target = action.get("target")
        port = action.get("port")
        allowed_ports = context.metadata.get("allowed_ports", ())
        if not target or not isinstance(port, int) or port < 1 or port > 65535:
            return AdapterResult("blocked", error="target_and_port_required")
        if allowed_ports and port not in set(allowed_ports):
            return AdapterResult("blocked", error="port_not_authorized")
        if action.get("scan_range", False):
            return AdapterResult("blocked", error="range_scan_not_allowed")
        return AdapterResult("completed")

    async def execute(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        validation = await self.validate(action, context)
        if validation.status != "completed":
            return validation
        response = await self.connector(str(action["target"]), int(action["port"]))
        return AdapterResult(
            "completed",
            observations=[{
                "target": action["target"],
                "port": action["port"],
                "transport_status": response.get("transport_status", response.get("status", "unknown")),
                "service": response.get("service", response.get("service_hint")),
                "confidence": response.get("confidence", response.get("service_confidence", 0.0)),
                "tls_sha256": response.get("tls_sha256"),
                "tls_error": bool(response.get("tls_error", False)),
                "error_type": response.get("error_type"),
                "banner": response.get("banner"),
                "probe_ms": response.get("probe_ms"),
                "source": self.manifest.name,
            }],
            cost_units=self.manifest.cost_units,
        )

    async def cancel(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        return AdapterResult("cancelled")
