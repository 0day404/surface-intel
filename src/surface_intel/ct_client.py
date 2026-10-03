from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from .adapters.ct import CtLogClient

DEFAULT_CT_ENDPOINT = "https://crt.sh/"

FetchFunc = Callable[[str, float], tuple[int, str]]


def _default_fetch(url: str, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", "replace")


class CrtShClient(CtLogClient):
    """Real certificate-transparency client over crt.sh's JSON API.

    The HTTP fetch is injectable so tests never touch the network. Statuses are
    structural (observed/empty/rate_limited/timeout/http_error/invalid_response)
    and never leak raw response bodies into logs — the caller persists the
    normalized certificate rows only.
    """

    def __init__(
        self,
        endpoint: str = DEFAULT_CT_ENDPOINT,
        timeout: float = 30.0,
        fetch: FetchFunc | None = None,
    ):
        self._endpoint = endpoint.rstrip("/") + "/"
        self._timeout = timeout
        self._fetch = fetch if fetch is not None else _default_fetch

    @property
    def endpoint(self) -> str:
        return self._endpoint

    async def health(self) -> dict[str, Any]:
        return {
            "ok": bool(self._endpoint.startswith("http")),
            "endpoint": self._endpoint,
            "timeout": self._timeout,
            "adapter": "ct_log",
        }

    async def search(self, domain: str) -> dict[str, Any]:
        normalized = domain.strip().rstrip(".").lower()
        query = urllib.parse.urlencode({"q": normalized, "output": "json"})
        url = f"{self._endpoint}?{query}"
        started = time.perf_counter()
        try:
            status_code, body = await asyncio.to_thread(self._fetch, url, self._timeout)
        except TimeoutError:
            return {"status": "timeout", "certificates": [], "queried_at": _now()}
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                return {"status": "rate_limited", "certificates": [], "queried_at": _now()}
            return {"status": "http_error", "certificates": [], "queried_at": _now(), "http_code": exc.code}
        except Exception as exc:  # transport death; classification stays coarse on purpose
            return {"status": "invalid_response", "certificates": [], "queried_at": _now(), "error_type": type(exc).__name__}
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        try:
            rows = json.loads(body)
        except json.JSONDecodeError:
            return {"status": "invalid_response", "certificates": [], "queried_at": _now()}
        if not isinstance(rows, list):
            return {"status": "invalid_response", "certificates": [], "queried_at": _now()}
        certificates = [row for row in (normalize_ct_row(item) for item in rows) if row is not None]
        status = "observed" if certificates else "empty"
        return {
            "status": status,
            "certificates": certificates,
            "log_source": "crt.sh",
            "queried_at": _now(),
            "query_ms": elapsed_ms,
        }


def normalize_ct_row(row: Any) -> dict[str, Any] | None:
    """Map one crt.sh JSON row to the stored certificate shape.

    `name_value` carries newline-separated SAN entries; wildcard entries keep
    their `*.` prefix here so the runner's scope classification can decide
    whether the wildcard base is authorized.
    """
    if not isinstance(row, dict):
        return None
    raw_names = str(row.get("name_value", "") or "")
    sans = [
        item.strip().rstrip(".").lower()
        for item in raw_names.splitlines()
        if item.strip()
    ]
    common_name = str(row.get("common_name", "") or "").strip().rstrip(".").lower() or None
    if common_name and common_name not in sans:
        sans.insert(0, common_name)
    return {
        "common_name": common_name,
        "sans": sans,
        "issuer": row.get("issuer_name"),
        "serial": row.get("serial_number"),
        "not_before": row.get("not_before"),
        "not_after": row.get("not_after"),
        "first_seen": row.get("entry_timestamp"),
        "last_seen": row.get("entry_timestamp"),
        "log_id": row.get("id"),
        "log_source": "crt.sh",
    }


def configured_ct_client(timeout: float = 30.0) -> CrtShClient:
    """Build the real CT client; endpoint defaults to env override then crt.sh."""
    endpoint = os.environ.get("SURFACE_INTEL_CT_ENDPOINT", "").strip() or DEFAULT_CT_ENDPOINT
    return CrtShClient(endpoint=endpoint, timeout=timeout)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
