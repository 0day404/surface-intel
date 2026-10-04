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


USER_AGENT = "surface-intel/0.1.0 (passive CT lookup)"


def _default_fetch(url: str, timeout: float, headers: dict[str, str] | None = None) -> tuple[int, str]:
    # An identifiable User-Agent is required: generic python-urllib UA gets 403
    # from ctlogs.dev (and is impolite to crt.sh).
    base = {"Accept": "application/json", "User-Agent": USER_AGENT}
    request = urllib.request.Request(url, headers={**base, **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", "replace")


def _primary_error(outcome: dict[str, Any]) -> str | None:
    """Best available primary failure detail (D1).

    Client failure dicts carry `http_code` / `error_type`, not an `error`
    key - the failover marker used to read `None` on every real failure.
    """
    if outcome.get("error"):
        return str(outcome["error"])
    if outcome.get("error_type"):
        return str(outcome["error_type"])
    if outcome.get("http_code") is not None:
        return f"http_code:{outcome['http_code']}"
    return None


class CrtShClient(CtLogClient):
    """Real certificate-transparency client over crt.sh's JSON API.

    The HTTP fetch is injectable so tests never touch the network. Statuses are
    structural (observed/empty/rate_limited/timeout/http_error/invalid_response)
    and never leak raw response bodies into logs — the caller persists the
    normalized certificate rows only.
    """

    source_name = "crt.sh"

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

    @property
    def timeout(self) -> float:
        return self._timeout

    async def health(self) -> dict[str, Any]:
        return {
            "ok": bool(self._endpoint.startswith("http")),
            # D1: configuration-only check - ok here never meant "reachable"
            "verified": False,
            "note": "configuration_check_only_no_network",
            "endpoint": self._endpoint,
            "timeout": self._timeout,
            "adapter": "ct_log",
        }

    async def probe(self) -> dict[str, Any]:
        """Real lightweight reachability probe (D1, opt-in via --probe).

        One tiny GET against the endpoint root; callers must gate this behind
        --real --confirm-real - the default health path stays network-free.
        """
        started = time.perf_counter()
        try:
            status_code, _body = await asyncio.to_thread(self._fetch, self._endpoint, self._timeout)
            return {"ok": True, "http_code": status_code,
                    "latency_ms": int((time.perf_counter() - started) * 1000)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                    "latency_ms": int((time.perf_counter() - started) * 1000)}

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


DEFAULT_CTLOGS_SEARCH = "https://ctlogs.dev/search"
DEFAULT_CTLOGS_DETAIL = "https://api.ctlogs.dev/v1/cert"


def normalize_ctlogs_row(row: Any) -> dict[str, Any] | None:
    """Map one ctlogs.dev search row to the stored certificate shape.

    Search rows reveal the matched identity only — no SAN list (`san_count`
    just states how many names the certificate carries). `sans` keeps the
    match and `san_completeness` stays `match_only` unless the detail backfill
    succeeds, so downstream consumers can tell partial SAN data from full.
    """
    if not isinstance(row, dict):
        return None
    match = str(row.get("match", "") or "").strip().rstrip(".").lower()
    return {
        "common_name": None,
        "sans": [match] if match else [],
        "issuer": row.get("issuer"),
        "serial": row.get("serial_hex"),
        "not_before": row.get("not_before"),
        "not_after": row.get("not_after"),
        "first_seen": None,
        "last_seen": None,
        "log_id": row.get("id"),
        "log_source": "ctlogs.dev",
        "san_count": row.get("san_count"),
        "san_completeness": "match_only",
    }


def apply_ctlogs_detail(row: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    """Enrich a match-only row in place from a ctlogs.dev cert detail payload."""
    sans = detail.get("san_dns")
    if isinstance(sans, list) and sans:
        row["sans"] = [str(item).strip().rstrip(".").lower() for item in sans if str(item).strip()]
        row["san_completeness"] = "full"
    common_name = detail.get("subject_cn")
    if common_name:
        row["common_name"] = str(common_name).strip().rstrip(".").lower()
    issuer_dn = detail.get("issuer_dn")
    if issuer_dn:
        row["issuer"] = issuer_dn  # full DN supersedes the search row's issuer CN
    sightings = detail.get("sightings")
    if isinstance(sightings, list) and sightings:
        stamps = sorted(
            str(item.get("sct_ts"))
            for item in sightings
            if isinstance(item, dict) and item.get("sct_ts")
        )
        if stamps:
            row["first_seen"] = stamps[0]
            row["last_seen"] = stamps[-1]
    return row


class CrtlogsDevClient(CtLogClient):
    """Alternative CT client over ctlogs.dev (crt.sh outage fallback).

    Each search consumes 1 + min(detail_limit, rows) HTTP requests; anonymous
    access is capped at 100 requests/hour, so the detail backfill defaults to
    a small number. Detail fetches are best-effort: any failure leaves the
    match-only search row in place and never fails the search itself.
    """

    source_name = "ctlogs.dev"

    def __init__(
        self,
        search_endpoint: str = DEFAULT_CTLOGS_SEARCH,
        detail_endpoint: str = DEFAULT_CTLOGS_DETAIL,
        timeout: float = 30.0,
        fetch: FetchFunc | None = None,
        api_key: str | None = None,
        detail_limit: int = 5,
    ):
        self._search_endpoint = search_endpoint.rstrip("/")
        self._detail_endpoint = detail_endpoint.rstrip("/")
        self._timeout = timeout
        self._api_key = api_key
        self._detail_limit = max(0, int(detail_limit))
        if fetch is not None:
            self._fetch = fetch
        elif api_key:
            headers = self._headers()
            self._fetch = lambda url, timeout_: _default_fetch(url, timeout_, headers)
        else:
            self._fetch = _default_fetch

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    @property
    def timeout(self) -> float:
        return self._timeout

    async def health(self) -> dict[str, Any]:
        return {
            "ok": bool(self._search_endpoint.startswith("http")),
            # D1: configuration-only check - ok here never meant "reachable"
            "verified": False,
            "note": "configuration_check_only_no_network",
            "endpoint": self._search_endpoint,
            "detail_endpoint": self._detail_endpoint,
            "detail_limit": self._detail_limit,
            "api_key_configured": bool(self._api_key),
            "adapter": "ct_log",
            "source": "ctlogs.dev",
        }

    async def probe(self) -> dict[str, Any]:
        """Real lightweight reachability probe (D1, opt-in via --probe)."""
        started = time.perf_counter()
        try:
            status_code, _body = await asyncio.to_thread(
                self._fetch, self._search_endpoint, self._timeout)
            return {"ok": True, "http_code": status_code,
                    "latency_ms": int((time.perf_counter() - started) * 1000)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200],
                    "latency_ms": int((time.perf_counter() - started) * 1000)}

    async def search(self, domain: str) -> dict[str, Any]:
        normalized = domain.strip().rstrip(".").lower()
        query = urllib.parse.urlencode({"q": normalized, "output": "json"})
        url = f"{self._search_endpoint}?{query}"
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
            payload = json.loads(body)
        except json.JSONDecodeError:
            return {"status": "invalid_response", "certificates": [], "queried_at": _now()}
        rows = payload.get("rows") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return {"status": "invalid_response", "certificates": [], "queried_at": _now()}
        certificates = [row for row in (normalize_ctlogs_row(item) for item in rows) if row is not None]
        await self._backfill_details(certificates)
        status = "observed" if certificates else "empty"
        return {
            "status": status,
            "certificates": certificates,
            "log_source": "ctlogs.dev",
            "queried_at": _now(),
            "query_ms": elapsed_ms,
        }

    async def _backfill_details(self, certificates: list[dict[str, Any]]) -> None:
        for row in certificates[: self._detail_limit]:
            cert_id = row.get("log_id")
            if not cert_id:
                continue
            url = f"{self._detail_endpoint}/{urllib.parse.quote(str(cert_id), safe='')}"
            try:
                _status_code, body = await asyncio.to_thread(self._fetch, url, self._timeout)
                detail = json.loads(body)
            except Exception:  # backfill is best-effort; the search row stays usable
                continue
            if isinstance(detail, dict):
                apply_ctlogs_detail(row, detail)


class FailoverCtClient(CtLogClient):
    """crt.sh first, ctlogs.dev on any non-definitive primary outcome.

    Failover triggers only on infrastructure failures (rate_limited, timeout,
    http_error, invalid_response, transport death) — definitive answers
    (`observed`, `empty`) are always trusted and never re-queried. Every
    switched outcome carries an explicit `failover` marker so stored evidence
    keeps predictable provenance; nothing is ever switched silently.
    """

    source_name = "auto(crt.sh->ctlogs.dev)"

    _DEFINITIVE = {"observed", "empty"}

    def __init__(self, primary: CtLogClient, fallback: CtLogClient):
        self._primary = primary
        self._fallback = fallback

    async def health(self) -> dict[str, Any]:
        primary_health = await self._primary.health()
        fallback_health = await self._fallback.health()
        return {
            "ok": bool(primary_health.get("ok") or fallback_health.get("ok")),
            "strategy": "failover",
            "source": self.source_name,
            # D1: ok means "one side is configured", never "verified reachable"
            "verified": False,
            "note": "configuration_check_only_no_network",
            "primary": primary_health,
            "fallback": fallback_health,
            "adapter": "ct_log",
        }

    async def probe(self) -> dict[str, Any]:
        """Real reachability probe of both endpoints (D1, opt-in via --probe)."""
        primary_probe = await self._primary.probe()
        fallback_probe = await self._fallback.probe()
        return {
            "ok": bool(primary_probe.get("ok") or fallback_probe.get("ok")),
            "strategy": "failover",
            "primary": primary_probe,
            "fallback": fallback_probe,
        }

    async def search(self, domain: str) -> dict[str, Any]:
        try:
            primary_outcome = await self._primary.search(domain)
        except Exception as exc:  # defensive: clients normally map their own errors
            primary_outcome = {"status": f"transport:{type(exc).__name__}"}
        if primary_outcome.get("status") in self._DEFINITIVE:
            return primary_outcome
        marker = {
            "from": getattr(self._primary, "source_name", "primary"),
            "reason": str(primary_outcome.get("status")),
            "primary_error": _primary_error(primary_outcome),
            "fallback": getattr(self._fallback, "source_name", "fallback"),
        }
        try:
            fallback_outcome = await self._fallback.search(domain)
        except Exception as exc:
            fallback_outcome = {"status": f"transport:{type(exc).__name__}", "certificates": []}
        return {**fallback_outcome, "failover": marker}


def configured_ct_client(timeout: float = 30.0) -> CtLogClient:
    """Build the real CT client; `SURFACE_INTEL_CT_SOURCE` picks the strategy.

    `auto` (default): crt.sh primary with ctlogs.dev failover — switched
    outcomes always carry an explicit `failover` marker in the observation.
    `crtsh` / `ctlogs`: pin a single source (no failover). crt.sh endpoint
    via `SURFACE_INTEL_CT_ENDPOINT`; ctlogs.dev via `SURFACE_INTEL_CT_API_KEY`
    (Bearer) and `SURFACE_INTEL_CT_DETAIL_LIMIT` (default 5). Unknown sources
    fail loudly rather than silently falling back to the wrong data source.
    """
    source = os.environ.get("SURFACE_INTEL_CT_SOURCE", "").strip().lower()
    if source in {"", "auto"}:
        return FailoverCtClient(
            CrtShClient(
                endpoint=os.environ.get("SURFACE_INTEL_CT_ENDPOINT", "").strip() or DEFAULT_CT_ENDPOINT,
                timeout=timeout,
            ),
            _configured_ctlogs_client(timeout),
        )
    if source == "crtsh":
        endpoint = os.environ.get("SURFACE_INTEL_CT_ENDPOINT", "").strip() or DEFAULT_CT_ENDPOINT
        return CrtShClient(endpoint=endpoint, timeout=timeout)
    if source == "ctlogs":
        return _configured_ctlogs_client(timeout)
    raise ValueError(f"unknown_ct_source:{source}")


def _configured_ctlogs_client(timeout: float) -> CrtlogsDevClient:
    api_key = os.environ.get("SURFACE_INTEL_CT_API_KEY", "").strip() or None
    raw_limit = os.environ.get("SURFACE_INTEL_CT_DETAIL_LIMIT", "").strip()
    try:
        detail_limit = int(raw_limit) if raw_limit else 5
    except ValueError:
        raise ValueError("invalid_ct_detail_limit") from None
    return CrtlogsDevClient(timeout=timeout, api_key=api_key, detail_limit=detail_limit)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
