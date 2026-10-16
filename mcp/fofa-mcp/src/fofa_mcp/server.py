from __future__ import annotations

import logging
from uuid import uuid4
import hashlib
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from .accounts import AccountManager
from .budget import BudgetRegistry
from .client import FofaClient
from .config import Settings, env_file_path
from .errors import FofaError, InvalidQueryError, PaginationError
from .formatters import format_error, format_operation, format_search, format_stats
from .logging import configure_logging
from .version import (
    CONFIG_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    SERVER_VERSION,
    TOOL_SCHEMA_VERSION,
)
from .queries import CursorTracker, QueryTracker, query_fingerprint
from .policy import (
    normalize_fields,
    normalize_stats_fields,
    validate_host,
    analyze_query,
    enforce_query_risk,
    validate_page,
    validate_output_format,
    validate_port_list,
    validate_query,
    validate_request_id,
    validate_size,
    validate_status_codes,
    validate_task_id,
)

logger = logging.getLogger(__name__)
mcp = FastMCP("fofa-mcp")
_service: tuple[
    Settings, FofaClient, BudgetRegistry, QueryTracker, CursorTracker
] | None = None

_SENSITIVE_ACCOUNT_KEYS = {
    "key",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "password",
    "secret",
    "authorization",
}


def _sanitize_account_info(value: Any, key: str = "") -> Any:
    if key.lower() in _SENSITIVE_ACCOUNT_KEYS:
        return None
    if isinstance(value, dict):
        return {
            name: sanitized
            for name, item in value.items()
            if (sanitized := _sanitize_account_info(item, str(name))) is not None
        }
    if isinstance(value, list):
        return [_sanitize_account_info(item) for item in value]
    if key.lower() == "email" and isinstance(value, str) and "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"
    return value


def _structured_query(
    domain: str,
    ip: str,
    port: str,
    host: str,
    body: str,
    icon_hash: str,
    icp: str,
    status_code: str,
) -> str:
    parts = []
    for name, value in (
        ("domain", domain), ("ip", ip), ("port", port), ("host", host),
        ("body", body), ("icon_hash", icon_hash), ("icp", icp),
    ):
        if value.strip():
            if name == "port":
                value = validate_port_list(value)
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            parts.append(f'{name}="{escaped}"')
    if status_code.strip():
        parts.append(f"status_code={validate_status_codes(status_code)}")
    if not parts:
        raise InvalidQueryError("provide at least one structured FOFA condition")
    return " && ".join(parts)


def _runtime() -> tuple[
    Settings, FofaClient, BudgetRegistry, QueryTracker, CursorTracker
]:
    global _service
    if _service is None:
        settings = Settings.load()
        _service = (
            settings,
            FofaClient(settings, AccountManager(settings.accounts)),
            BudgetRegistry(
                max_requests=settings.max_requests_per_session,
                max_estimated_fpoints=settings.max_estimated_fpoints,
                max_total_results=settings.max_total_results,
            ),
            QueryTracker(),
            CursorTracker(),
        )
    return _service


def _request_id(value: str) -> str:
    return validate_request_id(value) if value else f"req-{uuid4().hex}"


def _diagnostic_log(
    tool: str,
    request_id: str,
    task_id: str | None,
    started: float,
    error: Exception,
    query: str = "",
) -> None:
    normalized = " ".join(query.split())
    query_fp = (
        hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        if normalized
        else None
    )
    logger.warning(
        "tool=%s request_id=%s task_id=%s query_fp=%s account=%s "
        "duration_ms=%d cost=%s error=%s",
        tool,
        request_id,
        task_id,
        query_fp,
        getattr(error, "account", None),
        int((time.monotonic() - started) * 1000),
        None,
        type(error).__name__,
    )


@mcp.tool()
async def fofa_search(
    query: str,
    fields: list[str] | None = None,
    size: int = 100,
    page: int = 1,
    format: str = "standard",
    task_id: str = "default",
    confirm: bool = False,
    request_id: str = "",
) -> dict[str, Any]:
    """Run one page of a complete FOFA DSL query.

    Use this when the caller already has a FOFA DSL query. Use
    fofa_search_structured instead when the caller has ordinary domain/IP/port
    conditions. If the response has has_more=true and a next_token, call
    fofa_search_next rather than repeating this search. Use fofa_stats only when
    distribution analysis is needed, and fofa_host only for a selected asset.
    This reads indexed data; it does not scan or probe targets.
    """
    started = time.monotonic()
    try:
        request_id = _request_id(request_id)
        settings, client, budgets, queries, cursors = _runtime()
        task_id = validate_task_id(task_id)
        budget = budgets.get(task_id)
        query = validate_query(query, settings)
        budget.check_result_capacity()
        size = validate_size(size, settings)
        page = validate_page(page)
        selected = normalize_fields(fields)
        output_format = validate_output_format(format)
        risk = analyze_query(query, fields=selected, size=size)
        enforce_query_risk(risk, confirm)
        queries.reserve(
            task_id,
            query_fingerprint(
                query=query, fields=selected, mode="page", size=size, page=page
            ),
        )
        budget.reserve_request()
        result = await client.search(query, selected, size, page)
        budget.record_response(result)
        # FOFA search/all 的 next 游标登记后，fofa_search_next 才能按 README 流程续查。
        cursors.register(
            task_id,
            result.get("next", ""),
            query=query,
            fields=selected,
            full=False,
            next_page=page + 1,
        )
        return format_search(
            result,
            selected,
            budget,
            page_size=size,
            pagination_mode="page",
            output_format=output_format,
            risk=risk,
            request_id=request_id,
            task_id=task_id,
        )
    except Exception as error:
        _diagnostic_log("fofa_search", request_id, task_id, started, error, query)
        return format_error(error, request_id=request_id, task_id=task_id)


@mcp.tool()
async def fofa_search_structured(
    domain: str = "",
    ip: str = "",
    port: str = "",
    host: str = "",
    body: str = "",
    icon_hash: str = "",
    icp: str = "",
    status_code: str = "",
    fields: list[str] | None = None,
    size: int = 100,
    page: int = 1,
    format: str = "standard",
    task_id: str = "default",
    confirm: bool = False,
    request_id: str = "",
) -> dict[str, Any]:
    """Build a FOFA query from common conditions and run one page.

    Use this instead of manually composing DSL for domain, IP, port, host, body,
    icon hash, ICP, or status code inputs. Continue only with fofa_search_next
    when the result explicitly provides has_more=true and next_token.
    """
    started = time.monotonic()
    try:
        request_id = _request_id(request_id)
        settings, client, budgets, queries, cursors = _runtime()
        task_id = validate_task_id(task_id)
        budget = budgets.get(task_id)
        query = validate_query(
            _structured_query(domain, ip, port, host, body, icon_hash, icp, status_code),
            settings,
        )
        size = validate_size(size, settings)
        page = validate_page(page)
        selected = normalize_fields(fields)
        output_format = validate_output_format(format)
        risk = analyze_query(query, fields=selected, size=size)
        enforce_query_risk(risk, confirm)
        queries.reserve(
            task_id,
            query_fingerprint(
                query=query, fields=selected, mode="page", size=size, page=page
            ),
        )
        budget.check_result_capacity()
        budget.reserve_request()
        result = await client.search(query, selected, size, page)
        budget.record_response(result)
        cursors.register(
            task_id,
            result.get("next", ""),
            query=query,
            fields=selected,
            full=False,
            next_page=page + 1,
        )
        return format_search(
            result,
            selected,
            budget,
            page_size=size,
            pagination_mode="page",
            output_format=output_format,
            risk=risk,
            request_id=request_id,
            task_id=task_id,
        )
    except Exception as error:
        _diagnostic_log(
            "fofa_search_structured", request_id, task_id, started, error, locals().get("query", "")
        )
        return format_error(error, request_id=request_id, task_id=task_id)


@mcp.tool()
async def fofa_search_next(
    query: str,
    next_token: str = "",
    fields: list[str] | None = None,
    size: int = 100,
    full: bool = False,
    cursor_page: int = 1,
    format: str = "standard",
    task_id: str = "default",
    confirm: bool = False,
    request_id: str = "",
) -> dict[str, Any]:
    """Continue a FOFA cursor query using the previous response's next_token.

    Call only when a prior search returned a next_token. Keep query, fields, and
    full unchanged. Pass the returned token and increment cursor_page. Do not
    call this repeatedly after next_token is empty. full=true may increase scope
    or cost and requires confirm=true.
    """
    started = time.monotonic()
    try:
        request_id = _request_id(request_id)
        settings, client, budgets, queries, cursors = _runtime()
        task_id = validate_task_id(task_id)
        budget = budgets.get(task_id)
        query = validate_query(query, settings)
        if len(next_token) > 2048:
            raise PaginationError("next_token is too long")
        if cursor_page < 1 or cursor_page > settings.max_cursor_pages:
            raise PaginationError(
                f"cursor_page must be between 1 and {settings.max_cursor_pages}"
            )
        size = validate_size(size, settings)
        selected = normalize_fields(fields)
        output_format = validate_output_format(format)
        risk = analyze_query(query, fields=selected, size=size, full=full)
        enforce_query_risk(risk, confirm)
        cursors.validate(
            task_id,
            next_token,
            query=query,
            fields=selected,
            full=full,
            cursor_page=cursor_page,
        )
        queries.reserve(
            task_id,
            query_fingerprint(
                query=query,
                fields=selected,
                mode="cursor",
                size=size,
                next_token=next_token,
                full=full,
            ),
        )
        budget.check_result_capacity()
        budget.reserve_request()
        result = await client.search_next(query, selected, size, next_token, full)
        budget.record_response(result)
        cursors.register(
            task_id,
            result.get("next", ""),
            query=query,
            fields=selected,
            full=full,
            next_page=cursor_page + 1,
        )
        return format_search(
            result,
            selected,
            budget,
            page_size=size,
            pagination_mode="cursor",
            output_format=output_format,
            risk=risk,
            request_id=request_id,
            task_id=task_id,
        )
    except Exception as error:
        _diagnostic_log("fofa_search_next", request_id, task_id, started, error, query)
        return format_error(error, request_id=request_id, task_id=task_id)


@mcp.tool()
async def fofa_stats(
    query: str,
    fields: list[str] | None = None,
    top_n: int = 10,
    task_id: str = "default",
    request_id: str = "",
) -> dict[str, Any]:
    """Return FOFA aggregation statistics for a query.

    Call only when the caller needs distributions such as top titles, countries,
    ports, or products; do not call automatically after every search.
    """
    started = time.monotonic()
    try:
        request_id = _request_id(request_id)
        settings, client, budgets, _, _ = _runtime()
        task_id = validate_task_id(task_id)
        budget = budgets.get(task_id)
        query = validate_query(query, settings)
        selected = normalize_stats_fields(fields)
        if not 1 <= top_n <= 100:
            raise PaginationError("top_n must be between 1 and 100")
        budget.reserve_request()
        result = await client.stats(query, selected, top_n)
        budget.record_response(result)
        return format_stats(
            result,
            fields=selected,
            top_n=top_n,
            budget=budget,
            request_id=request_id,
            task_id=task_id,
        )
    except Exception as error:
        _diagnostic_log("fofa_stats", request_id, task_id, started, error, query)
        return format_error(error, request_id=request_id, task_id=task_id)


@mcp.tool()
async def fofa_host(
    host: str, detail: bool = False, task_id: str = "default", request_id: str = ""
) -> dict[str, Any]:
    """Get FOFA host aggregation for one selected IP or hostname.

    Use after search identifies a priority asset; this is not a bulk replacement
    for search and should not be called for every result by default.
    """
    started = time.monotonic()
    try:
        request_id = _request_id(request_id)
        _, client, budgets, _, _ = _runtime()
        task_id = validate_task_id(task_id)
        budget = budgets.get(task_id)
        budget.reserve_request()
        result = await client.host(validate_host(host), detail)
        account_used = result.get("_account_used")
        accounts_tried = result.get("_accounts_tried")
        result.pop("_account_used", None)
        result.pop("_accounts_tried", None)
        budget.record_response(result)
        return format_operation(
            data=result,
            budget=budget,
            account_used=account_used,
            accounts_tried=accounts_tried,
            summary={"host": host, "detail": detail},
            request_id=request_id,
            task_id=task_id,
        )
    except Exception as error:
        _diagnostic_log("fofa_host", request_id, task_id, started, error)
        return format_error(error, request_id=request_id, task_id=task_id)


@mcp.tool()
async def fofa_account_info(
    task_id: str = "default", request_id: str = ""
) -> dict[str, Any]:
    """Get account quota information without exposing API keys.

    Call when the user requests quota status, before a deliberately large task,
    or after a quota error. Do not call before every ordinary search.
    """
    started = time.monotonic()
    try:
        request_id = _request_id(request_id)
        settings, client, budgets, _, _ = _runtime()
        task_id = validate_task_id(task_id)
        budget = budgets.get(task_id)
        # info/my 是免费调用，不计请求预算。
        result = await client.account_info()
        account_used = result.get("_account_used")
        accounts_tried = result.get("_accounts_tried")
        result.pop("_account_used", None)
        result.pop("_accounts_tried", None)
        budget.record_response(result)
        return format_operation(
            data=_sanitize_account_info(result),
            budget=budget,
            account_used=account_used,
            accounts_tried=accounts_tried,
            summary={
                "accounts": [
                    {
                        "account": account.label,
                        "email": account.email[:1] + "***" + account.email[account.email.find("@") :],
                    }
                    if "@" in account.email
                    else {"account": account.label, "email": ""}
                    for account in settings.accounts
                ]
            },
            request_id=request_id,
            task_id=task_id,
        )
    except Exception as error:
        _diagnostic_log("fofa_account_info", request_id, task_id, started, error)
        return format_error(error, request_id=request_id, task_id=task_id)


@mcp.tool()
async def fofa_health(request_id: str = "") -> dict[str, Any]:
    """Check local configuration without making a remote FOFA request.

    Use at startup or for installation/configuration diagnosis; it does not consume FOFA quota.
    """
    started = time.monotonic()
    try:
        request_id = _request_id(request_id)
        settings, client, budgets, _, _ = _runtime()
        env_file = env_file_path()
        return {
            "ok": True,
            "schema_version": 1,
            "request_id": request_id,
            "task_id": None,
            "service": "fofa-mcp",
            "version": SERVER_VERSION,
            "tool_schema_version": TOOL_SCHEMA_VERSION,
            "result_schema_version": RESULT_SCHEMA_VERSION,
            "config_schema_version": CONFIG_SCHEMA_VERSION,
            "api_base": settings.api_base,
            "accounts_configured": len(settings.accounts),
            "accounts": [account.label for account in settings.accounts],
            "accounts_state": client.accounts.snapshot(),
            "configuration": {
                "env_file": str(env_file),
                "env_file_exists": env_file.is_file(),
                "api_base_configured": bool(settings.api_base),
            },
            "budget": budgets.snapshot(),
            "limits": {
                "default_page_size": settings.default_page_size,
                "max_page_size": settings.max_page_size,
                "max_cursor_pages": settings.max_cursor_pages,
            },
        }
    except Exception as error:
        _diagnostic_log("fofa_health", request_id, None, started, error)
        return format_error(error, request_id=request_id)


def main() -> None:
    try:
        settings = Settings.load()
        configure_logging(settings.log_level)
        logger.info("starting fofa-mcp accounts=%d", len(settings.accounts))
    except FofaError as error:
        configure_logging("INFO")
        logger.error("%s", error)
        raise SystemExit(2) from error
    mcp.run(transport="stdio")
