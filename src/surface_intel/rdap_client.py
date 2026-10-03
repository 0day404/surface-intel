from __future__ import annotations

import asyncio
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from .adapters.rdap import RdapLookupClient

DEFAULT_RDAP_ENDPOINT = "https://rdap.org/"
IANA_WHOIS_SERVER = "whois.iana.org"

FetchFunc = Callable[[str, float], tuple[int, str]]

_WHOIS_NOT_FOUND_MARKERS = ("not found", "no match", "no data found", "no entries found", "status: free")
_WHOIS_REFERRAL_PREFIXES = ("refer:", "whois:", "registrar whois server:")


def _default_fetch(url: str, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Accept": "application/rdap+json, application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", "replace")


class RdapOrgClient(RdapLookupClient):
    """Real RDAP client over rdap.org's bootstrap redirect service.

    The HTTP fetch is injectable so tests never touch the network. A 404 is a
    definitive negative for ip/asn (RIR coverage is complete); for domains the
    sequential client may still fall back to WHOIS because some TLDs publish
    no RDAP service at all.
    """

    _PATHS = {"domain": "domain", "ip": "ip", "asn": "autnum"}

    def __init__(
        self,
        endpoint: str = DEFAULT_RDAP_ENDPOINT,
        timeout: float = 20.0,
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
            "adapter": "rdap_lookup",
        }

    async def lookup(self, target: str, kind: str) -> dict[str, Any]:
        normalized = target.strip().rstrip(".").lower()
        path = self._PATHS[kind]
        query = urllib.parse.quote(normalized, safe="")
        url = f"{self._endpoint}{path}/{query}"
        started = time.perf_counter()
        try:
            status_code, body = await asyncio.to_thread(self._fetch, url, self._timeout)
        except TimeoutError:
            return {"status": "timeout", "queried_at": _now()}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"status": "not_found", "source_protocol": "rdap", "queried_at": _now()}
            if exc.code == 429:
                return {"status": "rate_limited", "queried_at": _now()}
            return {"status": "http_error", "queried_at": _now(), "http_code": exc.code}
        except Exception as exc:  # transport death; classification stays coarse on purpose
            return {"status": "invalid_response", "queried_at": _now(), "error_type": type(exc).__name__}
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return {"status": "invalid_response", "queried_at": _now()}
        if not isinstance(payload, dict):
            return {"status": "invalid_response", "queried_at": _now()}
        return {
            "status": "observed",
            "record": normalize_rdap_record(payload),
            "source_protocol": "rdap",
            "queried_at": _now(),
            "query_ms": elapsed_ms,
        }


class WhoisFallbackClient:
    """Minimal WHOIS port-43 client used only as an RDAP fallback.

    Queries the IANA registry first and follows at most one referral. The raw
    response text is never returned or logged — only a small allowlist of
    registration fields is extracted, and every record it produces must carry
    the `whois_fallback` data-quality marking.
    """

    _ALLOWLIST = (
        ("domain name", "name"),
        ("registrar", "registrar"),
        ("registrant organization", "registrant_org"),
        ("registrant organisation", "registrant_org"),
        ("creation date", "created"),
        ("created", "created"),
        ("registry expiry date", "expires"),
        ("expiry date", "expires"),
        ("name server", "name_server"),
    )

    def __init__(
        self,
        timeout: float = 15.0,
        server: str = IANA_WHOIS_SERVER,
        query_func: Callable[[str, str, float], str] | None = None,
    ):
        self._timeout = timeout
        self._server = server
        self._query_func = query_func if query_func is not None else _whois_query

    async def health(self) -> dict[str, Any]:
        return {
            "ok": bool(self._server),
            "server": self._server,
            "timeout": self._timeout,
            "adapter": "rdap_lookup",
            "role": "whois_fallback",
        }

    async def lookup(self, target: str, kind: str) -> dict[str, Any]:
        if kind != "domain":
            return {"status": "unsupported", "queried_at": _now()}
        normalized = target.strip().rstrip(".").lower()
        started = time.perf_counter()
        try:
            text = await asyncio.to_thread(self._query_func, self._server, normalized, self._timeout)
        except TimeoutError:
            return {"status": "timeout", "queried_at": _now()}
        except Exception as exc:
            return {"status": "invalid_response", "queried_at": _now(), "error_type": type(exc).__name__}
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        lowered = text.lower()
        if any(marker in lowered for marker in _WHOIS_NOT_FOUND_MARKERS):
            return {"status": "not_found", "source_protocol": "whois", "queried_at": _now()}
        return {
            "status": "observed",
            "record": self._extract(normalized, text),
            "source_protocol": "whois",
            "queried_at": _now(),
            "query_ms": elapsed_ms,
        }

    def _extract(self, target: str, text: str) -> dict[str, Any]:
        fields: dict[str, list[str]] = {}
        for line in text.splitlines():
            cleaned = line.strip()
            if ":" not in cleaned:
                continue
            key, _, value = cleaned.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if not value:
                continue
            for prefix, field in self._ALLOWLIST:
                if key == prefix or key.startswith(prefix):
                    fields.setdefault(field, []).append(value)
        nameservers = sorted({
            value.strip().rstrip(".").lower()
            for value in fields.get("name_server", ())
            if value.strip()
        })
        registrant_org = fields.get("registrant_org", [None])[0]
        organizations = [registrant_org] if registrant_org else []
        return {
            "handle": None,
            "name": fields.get("name", [target])[0],
            "country": None,
            "organizations": organizations,
            "registrar": fields.get("registrar", [None])[0],
            "abuse_contacts": [],  # WHOIS text is not mined for contact addresses
            "events": {
                "registration": fields.get("created", [None])[0],
                "expiration": fields.get("expires", [None])[0],
                "last_changed": None,
            },
            "cidrs": [],
            "statuses": [],
            "nameservers": nameservers,
        }


class SequentialLookupClient(RdapLookupClient):
    """RDAP first; WHOIS fallback only when the domain RDAP path came up empty.

    The fallback exists because some TLDs publish no RDAP service; WHOIS data
    is free-text and unverified, so every record it yields keeps the explicit
    `whois_fallback` data-quality marking through to storage.
    """

    def __init__(self, rdap: RdapOrgClient, whois: WhoisFallbackClient):
        self._rdap = rdap
        self._whois = whois

    async def health(self) -> dict[str, Any]:
        rdap_health = await self._rdap.health()
        whois_health = await self._whois.health()
        return {**rdap_health, "whois_fallback": whois_health}

    async def lookup(self, target: str, kind: str) -> dict[str, Any]:
        outcome = await self._rdap.lookup(target, kind)
        if not (kind == "domain" and outcome.get("status") == "not_found"):
            return outcome
        fallback = await self._whois.lookup(target, kind)
        if fallback.get("status") != "observed":
            return outcome  # keep the RDAP 404 definitive; fallback adds nothing
        return {
            **fallback,
            "record": {**fallback.get("record", {}), "data_quality": "whois_fallback"},
        }


def normalize_rdap_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize an RDAP JSON object into the stored registration-record shape.

    Only structural fields are kept: organization/registrar names from entity
    vCards, abuse-role email addresses, event dates, CIDR literals and status
    tokens. Raw response text and free-form remarks are dropped.
    """
    entities = payload.get("entities") if isinstance(payload.get("entities"), list) else []
    registrar: str | None = None
    organizations: list[str] = []
    abuse_contacts: list[str] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        roles = {str(role) for role in entity.get("roles", ())}
        name = _vcard_value(entity.get("vcardArray"), "fn")
        email = _vcard_value(entity.get("vcardArray"), "email")
        if "registrar" in roles and name and registrar is None:
            registrar = name
        if roles & {"registrant", "administrative", "technical"} and name and name not in organizations:
            organizations.append(name)
        if "abuse" in roles and email and email not in abuse_contacts:
            abuse_contacts.append(email)
    events: dict[str, str | None] = {"registration": None, "expiration": None, "last_changed": None}
    event_map = {"registration": "registration", "expiration": "expiration", "last changed": "last_changed",
                 "last update of rdap database": None}
    for event in payload.get("events", ()) or ():
        if not isinstance(event, dict):
            continue
        field = event_map.get(str(event.get("eventAction", "")).lower())
        if field and events.get(field) is None:
            events[field] = event.get("eventDate")
    cidrs = [
        f"{item.get('v4', item.get('v6'))}/{item.get('value')}"
        if isinstance(item, dict) and (item.get("v4") or item.get("v6"))
        else None
        for item in (payload.get("cidr0_cidrs") or ())
    ]
    return {
        "handle": payload.get("handle"),
        "name": payload.get("ldhName") or payload.get("name") or payload.get("mpName"),
        "country": payload.get("country"),
        "organizations": organizations,
        "registrar": registrar,
        "abuse_contacts": abuse_contacts,
        "events": events,
        "cidrs": [item for item in cidrs if item],
        "ip_version": payload.get("ipVersion"),
        "start_address": payload.get("startAddress"),
        "end_address": payload.get("endAddress"),
        "asn_start": payload.get("startAutnum"),
        "asn_end": payload.get("endAutnum"),
        "statuses": [str(item) for item in (payload.get("status") or ())],
        "nameservers": [
            str(ns.get("ldhName", "")).rstrip(".").lower()
            for ns in (payload.get("nameservers") or ())
            if isinstance(ns, dict) and ns.get("ldhName")
        ],
    }


def _vcard_value(vcard_array: Any, property_name: str) -> str | None:
    if not isinstance(vcard_array, list) or len(vcard_array) != 2:
        return None
    for entry in vcard_array[1]:
        if isinstance(entry, (list, tuple)) and len(entry) >= 4 and entry[0] == property_name:
            return str(entry[3])
    return None


def _whois_query(server: str, query: str, timeout: float) -> str:
    with socket.create_connection((server, 43), timeout=timeout) as connection:
        connection.sendall(f"{query}\r\n".encode())
        chunks: list[bytes] = []
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    text = b"".join(chunks).decode("utf-8", "replace")
    for line in text.splitlines():
        stripped = line.strip().lower()
        if any(stripped.startswith(prefix) for prefix in _WHOIS_REFERRAL_PREFIXES):
            referred = stripped.split(":", 1)[1].strip()
            if referred and referred != server:
                return _whois_query(referred, query, timeout)
    return text


def configured_rdap_client(timeout: float = 20.0, whois_timeout: float = 15.0) -> SequentialLookupClient:
    """Build the real client; endpoint defaults to env override then rdap.org."""
    endpoint = os.environ.get("SURFACE_INTEL_RDAP_ENDPOINT", "").strip() or DEFAULT_RDAP_ENDPOINT
    return SequentialLookupClient(
        RdapOrgClient(endpoint=endpoint, timeout=timeout),
        WhoisFallbackClient(timeout=whois_timeout),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
