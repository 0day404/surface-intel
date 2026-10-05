from __future__ import annotations

import asyncio
import hashlib
import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Callable

from . import __version__

# Only low-impact methods ever leave the runner; everything else is refused
# in adapter validation.
ALLOWED_METHODS = ("GET", "HEAD")
MAX_REDIRECTS = 5

# Response headers are stored in full: security headers (CSP/HSTS/X-Frame-
# Options) and Set-Cookie traits are exactly the evidence remote triage needs,
# and nothing here is a secret of ours - cookies were set BY the target.
BODY_AND_HEADERS_NOTE = "headers persisted verbatim; bodies are digest-only"

class FetchTimeout(TimeoutError):
    """Timeout tagged with the phase it hit: "connect" or "app_layer".

    "connect" means the TCP (or TLS handshake) never completed; "app_layer"
    means the pipe is up but the service never answered - the distinction the
    58.210.136.131:8601 case (FOFA saw it open, HTTP probe hung) needed.
    """

    def __init__(self, phase: str):
        super().__init__(phase)
        self.phase = phase

FetchHop = Callable[[str, str, float], tuple[int, dict[str, str], bytes, bytes | None]]


def _default_fetch_hop(url: str, method: str, timeout: float):
    """One manual HTTP(S) hop over http.client (no automatic redirects).

    Returns (http_status, headers, body_bytes, peer_cert_der_or_None).
    http.client never follows redirects on its own, which is exactly what the
    safe-observation loop needs: every hop is re-authorized before connecting.
    Timeouts raise FetchTimeout carrying their phase so remote triage can tell
    "port unreachable" from "TCP up, service mute".
    """
    parts = urllib.parse.urlsplit(url)
    target = f"{parts.path or '/'}"
    if parts.query:
        target += f"?{parts.query}"
    peer = None
    if parts.scheme == "https":
        context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(
            parts.hostname, parts.port or 443, timeout=timeout, context=context)
    else:
        connection = http.client.HTTPConnection(
            parts.hostname, parts.port or 80, timeout=timeout)
    try:
        try:
            connection.connect()  # TCP (+TLS handshake) phase
            # grab the peer cert right after the handshake: the certificate is
            # fixed at connect time, and servers answering `Connection: close`
            # (Citrix/NetScaler style) make http.client tear the socket down
            # inside getresponse() - self.sock is None by the time we read it
            if parts.scheme == "https" and connection.sock is not None:
                peer = connection.sock.getpeercert(binary_form=True)
        except TimeoutError:
            raise FetchTimeout("connect") from None
        try:
            connection.request(method, target, headers={"Accept": "*/*", "User-Agent": f"surface-intel/{__version__}"})
            response = connection.getresponse()
            body = response.read(_BODY_CAP)
        except TimeoutError:
            raise FetchTimeout("app_layer") from None
        headers = {key.lower(): value for key, value in response.getheaders()}
        return response.status, headers, body, peer
    finally:
        connection.close()


_BODY_CAP = 262144  # digest-only observation; bodies are never persisted


def _default_resolve(host: str) -> list[str]:
    return [item[4][0] for item in socket.getaddrinfo(host, None)]


class SafeHttpFetchClient:
    """Real low-impact HTTP observation client (GET/HEAD, manual redirects).

    Every hop is re-authorized: the host must be on `allowed_hosts` (the
    confirmed scope) and must resolve to public addresses - private, loopback,
    link-local and reserved targets are refused before connecting. Bodies are
    read up to a cap and reduced to a SHA-256 digest; they are never stored.
    Statuses are structural: observed / timeout / tls_error / http_error /
    redirect_blocked / private_blocked, with response_class splitting
    200/404/403/429/other per plan R6.1.
    """

    source_name = "safe_http"

    def __init__(
        self,
        timeout: float = 15.0,
        max_redirects: int = MAX_REDIRECTS,
        max_body_bytes: int = _BODY_CAP,
        fetch_hop: FetchHop | None = None,
        resolver: Callable[[str], list[str]] | None = None,
    ):
        self._timeout = timeout
        self._max_redirects = max_redirects
        self._max_body_bytes = max_body_bytes
        self._fetch_hop = fetch_hop or _default_fetch_hop
        self._resolve = resolver or _default_resolve

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "timeout": self._timeout, "adapter": "safe_http",
                "methods": list(ALLOWED_METHODS)}

    async def observe(self, url: str, *, method: str = "GET",
                      allowed_hosts: tuple[str, ...] = ()) -> dict[str, Any]:
        method = method.upper()
        if method not in ALLOWED_METHODS:
            return {"status": "http_error", "error_type": "method_not_allowed", "queried_at": _now()}
        current = url.strip()
        chain: list[dict[str, Any]] = []
        started = time.perf_counter()
        for _hop in range(self._max_redirects + 1):
            parts = urllib.parse.urlsplit(current)
            host = (parts.hostname or "").lower()
            if parts.scheme not in {"http", "https"} or not host:
                return {"status": "http_error", "error_type": "invalid_url",
                        "redirect_chain": chain, "queried_at": _now()}
            if allowed_hosts and not _host_allowed(host, allowed_hosts):
                return {"status": "redirect_blocked", "redirect_chain": chain,
                        "blocked_url": current, "blocked_host": host, "queried_at": _now()}
            private = await asyncio.to_thread(self._private_ip, host)
            if private:
                return {"status": "private_blocked", "redirect_chain": chain,
                        "blocked_address": private, "queried_at": _now()}
            try:
                status, headers, body, peer = await asyncio.to_thread(
                    self._fetch_hop, current, method, self._timeout)
                body = body[: self._max_body_bytes]
            except FetchTimeout as exc:
                return {"status": "timeout", "error_type": f"timeout_{exc.phase}",
                        "redirect_chain": chain, "queried_at": _now()}
            except TimeoutError:
                # injected fetch_hop implementations may raise bare TimeoutError
                # with no phase info; the conservative label is connect
                return {"status": "timeout", "error_type": "timeout_connect",
                        "redirect_chain": chain, "queried_at": _now()}
            except ssl.SSLError as exc:
                kind = "cert_verify" if isinstance(exc, ssl.SSLCertVerificationError) else "handshake"
                # record the concrete handshake failure so remote triage can
                # tell a certificate mismatch from a protocol/version problem;
                # the hint flags that the same host often answers on plain
                # http:// (R3: pre/dev/biolox were 401-reachable on :80 only)
                return {"status": "tls_error", "error_type": type(exc).__name__,
                        "tls_failure_kind": kind, "error": str(exc)[:200],
                        "hint": "plain_http_may_be_available",
                        "redirect_chain": chain, "queried_at": _now()}
            except (ConnectionError, OSError) as exc:
                return {"status": "http_error", "error_type": type(exc).__name__,
                        "redirect_chain": chain, "queried_at": _now()}
            chain.append({
                "url": current,
                "http_status": status,
                "tls_sha256": hashlib.sha256(peer).hexdigest() if peer else None,
            })
            location = headers.get("location")
            if 300 <= status < 400 and location:
                if len(chain) > self._max_redirects:
                    return {"status": "http_error", "error_type": "too_many_redirects",
                            "redirect_chain": chain, "queried_at": _now()}
                # keep the raw Location value on the hop: where a blocked
                # redirect was headed is harmless, high-value context (B4)
                chain[-1]["location"] = location
                current = urllib.parse.urljoin(current, location)
                continue
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return {
                "status": "observed",
                "http_status": status,
                "response_class": _response_class(status),
                "final_url": current,
                "redirect_chain": chain,
                "headers": dict(headers),
                "content_type": headers.get("content-type"),
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "body_bytes": len(body),
                "body_truncated": len(body) >= self._max_body_bytes,
                "tls_sha256": chain[-1].get("tls_sha256"),
                "response_ms": elapsed_ms,
                "queried_at": _now(),
            }
        return {"status": "http_error", "error_type": "too_many_redirects",
                "redirect_chain": chain, "queried_at": _now()}

    def _private_ip(self, host: str) -> str | None:
        try:
            addresses = self._resolve(host)
        except (socket.gaierror, OSError):
            return None  # connection errors surface at fetch time
        for address in addresses:
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                continue
            if (parsed.is_private or parsed.is_loopback or parsed.is_link_local
                    or parsed.is_reserved or parsed.is_multicast):
                return address
        return None


def _host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    for raw_entry in allowed_hosts:
        entry = raw_entry.strip().lower()
        if entry == "*":
            # bare "*" is the documented full-wildcard scope (SKILL scope
            # template "authorized_domains": ["*"]) - authorize any host
            return True
        entry = entry.lstrip("*.").rstrip(".")
        if not entry:
            continue
        if host == entry or host.endswith("." + entry):
            return True
    return False


def _response_class(status: int) -> str:
    if 200 <= status < 300:
        return "ok"
    if status == 404:
        return "not_found"
    if status == 403:
        return "forbidden"
    if status == 429:
        return "rate_limited"
    return "other"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
