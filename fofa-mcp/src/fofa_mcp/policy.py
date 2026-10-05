from __future__ import annotations

import re
import ipaddress
from typing import Any

from .config import Settings
from .errors import InvalidFieldError, InvalidQueryError, PolicyDeniedError

DEFAULT_FIELDS = (
    "ip",
    "port",
    "protocol",
    "host",
    "domain",
    "icp",
    "title",
    "product",
    "version",
    "lastupdatetime",
)
ALL_FIELDS = (
    "ip", "port", "protocol", "country", "country_name", "region", "city",
    "longitude", "latitude", "as_number", "as_organization", "host", "domain",
    "os", "server", "icp", "title", "jarm", "header", "banner", "base_protocol",
    "link", "cert", "certs_issuer_org", "certs_issuer_cn", "certs_subject_org",
    "certs_subject_cn", "tls_ja3s", "tls_version", "product", "product_category",
    "version", "lastupdatetime", "cname", "status_code",
)
FIELD_SET = set(ALL_FIELDS)
STATS_DEFAULT_FIELDS = ("title", "country")
STATS_FIELDS = (
    "ip",
    "port",
    "protocol",
    "base_protocol",
    "country",
    "country_name",
    "region",
    "city",
    "as_number",
    "as_organization",
    "host",
    "domain",
    "os",
    "server",
    "icp",
    "title",
    "product",
    "product_category",
    "version",
    "cname",
    "status_code",
)
STATS_FIELD_SET = set(STATS_FIELDS)
HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9][A-Za-z0-9.:-]*[A-Za-z0-9]$")
OUTPUT_FORMATS = {"compact", "standard", "raw"}


def validate_query(query: str, settings: Settings) -> str:
    value = query.strip()
    if not value:
        raise InvalidQueryError("query must not be empty")
    if len(value) > settings.max_query_length:
        raise InvalidQueryError(f"query exceeds {settings.max_query_length} characters")
    if value in {"*", "*=\"\"", "domain=\"\""}:
        raise PolicyDeniedError("query is too broad; provide a meaningful filter")
    return value


def normalize_fields(fields: list[str] | None) -> list[str]:
    if not fields:
        return list(DEFAULT_FIELDS)
    cleaned = []
    for field in fields:
        value = field.strip()
        if not value:
            continue
        if value == "all":
            return list(ALL_FIELDS)
        if value not in FIELD_SET:
            raise InvalidFieldError(f"unsupported FOFA field: {value}")
        if value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        raise InvalidFieldError("fields must contain at least one valid field")
    return cleaned


def normalize_stats_fields(fields: list[str] | None) -> list[str]:
    """Validate fields accepted by FOFA's aggregation endpoint."""
    if not fields:
        return list(STATS_DEFAULT_FIELDS)
    cleaned = []
    for field in fields:
        value = field.strip()
        if not value:
            continue
        if value not in STATS_FIELD_SET:
            raise InvalidFieldError(f"unsupported FOFA stats field: {value}")
        if value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        raise InvalidFieldError("stats fields must contain at least one valid field")
    return cleaned


def validate_size(size: int, settings: Settings) -> int:
    if not 1 <= size <= settings.max_page_size:
        raise PolicyDeniedError(f"size must be between 1 and {settings.max_page_size}")
    return size


def validate_page(page: int) -> int:
    if page < 1:
        raise PolicyDeniedError("page must be at least 1")
    return page


def validate_host(host: str) -> str:
    value = host.strip()
    candidate = value[1:-1] if value.startswith("[") and value.endswith("]") else value
    try:
        ipaddress.ip_address(candidate)
        return candidate
    except ValueError:
        pass
    if not HOST_RE.fullmatch(value) or value.startswith(".") or value.endswith("."):
        raise PolicyDeniedError("host must be an IP address or hostname")
    return value


def validate_output_format(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in OUTPUT_FORMATS:
        raise PolicyDeniedError("format must be one of: compact, standard, raw")
    return normalized


def _validate_numeric_list(value: str, *, name: str, low: int, high: int) -> str:
    """逗号分隔的整数或 int-int 区间；其余拒绝，防止 DSL 注入。"""
    normalized = value.strip()
    if not normalized:
        raise PolicyDeniedError(f"{name} must not be empty")
    for segment in normalized.split(","):
        item = segment.strip()
        bounds = item.split("-", 1) if "-" in item else (item,)
        try:
            numbers = [int(part.strip()) for part in bounds]
        except ValueError as exc:
            raise PolicyDeniedError(f"{name} must contain integers or int-int ranges") from exc
        if not all(low <= number <= high for number in numbers):
            raise PolicyDeniedError(f"{name} values must be between {low} and {high}")
        if len(numbers) == 2 and numbers[0] > numbers[1]:
            raise PolicyDeniedError(f"{name} range must be ascending")
    return normalized


def validate_status_codes(value: str) -> str:
    return _validate_numeric_list(value, name="status_code", low=100, high=599)


def validate_port_list(value: str) -> str:
    return _validate_numeric_list(value, name="port", low=1, high=65535)


def validate_task_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise PolicyDeniedError("task_id must be between 1 and 128 characters")
    if any(char.isspace() for char in normalized):
        raise PolicyDeniedError("task_id must not contain whitespace")
    return normalized


def validate_request_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise PolicyDeniedError("request_id must be between 1 and 128 characters")
    if any(char.isspace() for char in normalized):
        raise PolicyDeniedError("request_id must not contain whitespace")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", normalized):
        raise PolicyDeniedError("request_id contains unsupported characters")
    return normalized


def analyze_query(
    query: str,
    *,
    fields: list[str],
    size: int,
    full: bool = False,
) -> dict[str, Any]:
    lower = query.lower()
    warnings: list[str] = []
    risk = "low"
    anchors = (
        "domain=", "ip=", "host=", "port=", "cert=", "title=", "body=",
        "icon_hash=", "org=", "asn=", "product=", "server=", "protocol=",
        "country=", "region=", "status_code=",
    )
    if not any(anchor in lower for anchor in anchors):
        risk = "high"
        warnings.append("query has no recognized asset anchor and may be very broad")
    if full:
        risk = "high"
        warnings.append("full search may increase scope or cost")
    if size > 200:
        if risk == "low":
            risk = "medium"
        warnings.append("large page size may increase response size and cost")
    if len(fields) > 15:
        if risk == "low":
            risk = "medium"
        warnings.append("many fields may increase response size")
    return {
        "risk": risk,
        "warnings": warnings,
        "requires_confirmation": risk == "high",
        "estimated_cost": max(1, (size + 99) // 100),
    }


def enforce_query_risk(analysis: dict[str, Any], confirmed: bool) -> None:
    if analysis["requires_confirmation"] and not confirmed:
        raise PolicyDeniedError(
            "high-risk query requires explicit confirmation; set confirm=true"
        )
