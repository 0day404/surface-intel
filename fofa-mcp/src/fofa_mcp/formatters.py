from __future__ import annotations

from typing import Any

from .budget import Budget
from .errors import ResponseSchemaError


def _map_search_rows(rows: list[Any], fields: list[str]) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if isinstance(row, dict):
            mapped.append(row)
            continue
        if not isinstance(row, list):
            raise ResponseSchemaError(
                f"FOFA result row {index} must be an object or array"
            )
        if len(row) != len(fields):
            raise ResponseSchemaError(
                f"FOFA result row {index} has {len(row)} values; expected {len(fields)}"
            )
        mapped.append(dict(zip(fields, row)))
    return mapped


def _normalize_stat_rows(results: Any) -> list[dict[str, Any]]:
    if isinstance(results, dict):
        normalized: list[dict[str, Any]] = []
        for field, values in results.items():
            if isinstance(values, dict):
                normalized.extend(
                    {"field": field, "value": value, "count": count}
                    for value, count in values.items()
                )
            elif isinstance(values, list):
                for row in values:
                    if isinstance(row, list) and len(row) >= 2:
                        normalized.append(
                            {"field": field, "value": row[0], "count": row[1]}
                        )
                    elif isinstance(row, dict):
                        normalized.append({"field": field, **row})
                    else:
                        normalized.append({"field": field, "value": row})
            else:
                normalized.append({"field": field, "value": values})
        return normalized
    if isinstance(results, list):
        return [
            row if isinstance(row, dict) else {"value": row}
            for row in results
        ]
    if results is None:
        return []
    return [{"value": results}]


def format_search(
    data: dict[str, Any],
    fields: list[str],
    budget: Budget,
    *,
    page_size: int,
    pagination_mode: str,
    output_format: str = "standard",
    risk: dict[str, Any] | None = None,
    complete: bool = False,
    request_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    rows = data.get("results") or []
    next_token = data.get("next", "")
    # 用 FOFA 返回的命中总数判定 has_more；拿行数==page_size 猜会把"恰好整页"误报成还有更多。
    total = data.get("size")
    if isinstance(total, int) and total >= 0:
        has_more = bool(next_token) or data.get("page", 1) * page_size < total
    else:
        has_more = bool(next_token)
    mapped = _map_search_rows(rows, fields)
    if output_format == "compact":
        compact_fields = ("ip", "port", "protocol", "host", "domain", "title")
        output_data = [
            {key: item[key] for key in compact_fields if key in item}
            for item in mapped
            if isinstance(item, dict)
        ]
    elif output_format == "raw":
        output_data = rows
    else:
        output_data = mapped
    result = {
        "ok": True,
        "schema_version": 1,
        "request_id": request_id,
        "task_id": task_id,
        "format": output_format,
        "risk": risk or {
            "risk": "low",
            "warnings": [],
            "requires_confirmation": False,
        },
        "query": data.get("query"),
        "fields": fields,
        "account": {
            "used": data.get("_account_used"),
            "tried": data.get("_accounts_tried", []),
            "switched": len(data.get("_accounts_tried", [])) > 1,
        },
        "summary": {
            "page": data.get("page", 1),
            "page_size": page_size,
            "count": len(rows),
            "has_more": has_more,
        },
        "pagination": {
            "mode": pagination_mode,
            "page": data.get("page", 1),
            "next_token": next_token,
        },
        "cost": {
            "consumed_fpoint": data.get("consumed_fpoint"),
            "required_fpoints": data.get("required_fpoints"),
        },
        "collection": {
            "completeness": "complete" if complete else "bounded",
            "records_this_page": len(rows),
            "records_seen_this_session": budget.results_seen,
            "has_more": has_more,
            "stopped_reason": None if has_more else "no_more_results",
        },
        "budget": budget.snapshot(),
        "data": output_data,
    }
    return result


def format_stats(
    data: dict[str, Any],
    *,
    fields: list[str],
    top_n: int,
    budget: Budget,
    request_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    rows = _normalize_stat_rows(data.get("results"))
    return {
        "ok": True,
        "schema_version": 1,
        "request_id": request_id,
        "task_id": task_id,
        "summary": {
            "query": data.get("query"),
            "fields": fields,
            "top_n": top_n,
            "groups": len(rows),
        },
        "pagination": {"mode": "none"},
        "account": {
            "used": data.get("_account_used"),
            "tried": data.get("_accounts_tried", []),
            "switched": len(data.get("_accounts_tried", [])) > 1,
        },
        "cost": {
            "consumed_fpoint": data.get("consumed_fpoint"),
            "required_fpoints": data.get("required_fpoints"),
        },
        "budget": budget.snapshot(),
        "data": rows,
    }


def format_error(
    error: Exception, *, request_id: str | None = None, task_id: str | None = None
) -> dict[str, Any]:
    error_type = getattr(error, "error_type", "internal_error")
    return {
        "ok": False,
        "schema_version": 1,
        "request_id": request_id,
        "task_id": task_id,
        "error": {
            "type": error_type,
            "message": str(error),
            "retryable": bool(getattr(error, "retryable", False)),
            "account": getattr(error, "account", None),
        },
    }


def format_operation(
    *,
    data: Any,
    budget: Budget,
    account_used: str | None = None,
    accounts_tried: list[str] | None = None,
    summary: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
    pagination: dict[str, Any] | None = None,
    request_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    tried = accounts_tried or ([account_used] if account_used else [])
    return {
        "ok": True,
        "schema_version": 1,
        "request_id": request_id,
        "task_id": task_id,
        "summary": summary or {},
        "pagination": pagination or {"mode": "none"},
        "account": {
            "used": account_used,
            "tried": tried,
            "switched": len(tried) > 1,
        },
        "cost": cost or {
            "consumed_fpoint": None,
            "required_fpoints": None,
        },
        "budget": budget.snapshot(),
        "data": data,
    }
