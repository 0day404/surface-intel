from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from ..policy.scope import Scope
from ..states import ActionStatus
from .base import AdapterManifest, AdapterResult, TaskContext


class PlaywrightAdapter:
    """Adapter boundary for external Playwright MCP; no Playwright source is vendored."""

    manifest = AdapterManifest(
        "playwright_mcp",
        "external",
        ("browser_navigation", "network_observation", "screenshots"),
        required_active_level="safe_discovery",
        supports_resume=False,
        supports_cancellation=True,
        external_side_effects=True,
        sensitive_data_policy="redact_and_reject",
    )
    launch_command = "npx -y @playwright/mcp@latest"

    def __init__(self, call_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None):
        self.call_tool = call_tool

    async def health(self, context: TaskContext) -> AdapterResult:
        if self.call_tool is None:
            return AdapterResult("unknown", error="not_configured")
        result = await self.call_tool("health", {})
        return AdapterResult("completed" if result.get("ok") else "failed", request_id=result.get("request_id"), error=result.get("error"))

    async def validate(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        target = str(action.get("lookup_target", "")).strip() or str(action.get("target", ""))
        host = urlsplit(target).hostname
        if not host or urlsplit(target).scheme not in {"http", "https"}:
            return AdapterResult("blocked", error="browser_target_required")
        if not context.scope_confirmed or context.active_level not in {"safe_discovery", "safe_probe"}:
            return AdapterResult("blocked", error="browser_authorization_required")
        # 与 runner 门控用同一个 Scope 判定（scope_payload 优先）；旧注入路径
        # （无 scope_payload）退回 allowed_domains 后缀匹配，保持兼容。
        scope_payload = context.metadata.get("scope_payload")
        if scope_payload:
            host_allowed = Scope.from_dict(scope_payload).is_in_scope(host)
        else:
            allowed_domains = tuple(context.metadata.get("allowed_domains", ()))
            host_allowed = (
                not allowed_domains
                or any(host == domain or host.endswith("." + domain.lstrip("*.")) for domain in allowed_domains)
            )
        if not host_allowed:
            return AdapterResult("blocked", error="browser_domain_not_authorized")
        if action.get("allow_login") or action.get("allow_state_change") or action.get("allow_download"):
            return AdapterResult("blocked", error="browser_side_effect_not_allowed")
        if self.call_tool is None:
            return AdapterResult("blocked", error="not_configured")
        return AdapterResult("completed")

    async def execute(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        validation = await self.validate(action, context)
        if validation.status != "completed":
            return validation
        anchor = str(action["target"])
        target = str(action.get("lookup_target", "")).strip() or anchor
        result = await self.call_tool("observe_public_page", {"url": target})
        outcome_status = str(result.get("status", "failed"))
        action_status = (ActionStatus.COMPLETED.value
                         if outcome_status in {"observed", "completed"}
                         else ActionStatus.FAILED.value)
        return AdapterResult(
            action_status,
            observations=[{
                "page_url": target,
                "scope_anchor": anchor if target != anchor else None,
                "final_url": result.get("final_url", target),
                "title": result.get("title", ""),
                # client 产出键是 http_status/network_summary（D5 键位统一）；
                # observation 落库键保持 status_code/network_summary 不变，
                # merger/export 的 schema 消费侧依赖这两个键名。
                "status_code": result.get("http_status"),
                "network_summary": result.get("network_summary", []),
                "partial_collection_errors": result.get("partial_collection_errors", []),
                "third_party_origins": result.get("third_party_origins", []),
                "console_error_count": result.get("console_error_count", 0),
                "screenshot_ref": result.get("screenshot_ref", ""),
                "source": self.manifest.name,
            }],
            evidence_refs=result.get("evidence_refs", []),
            request_id=result.get("request_id"),
            error=result.get("error") if action_status == ActionStatus.FAILED.value else None,
        )

    async def cancel(self, action: dict[str, Any], context: TaskContext) -> AdapterResult:
        return AdapterResult("cancelled")
