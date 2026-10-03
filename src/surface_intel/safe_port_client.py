from __future__ import annotations

import asyncio
import hashlib
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Any, Callable

# Well-known port -> protocol hints. These are guesses with bounded
# confidence - an open port never proves which service actually answers.
PORT_HINTS = {
    25: "smtp", 53: "dns", 80: "http", 110: "pop3", 143: "imap",
    443: "https", 465: "smtps", 587: "smtp", 993: "imaps", 995: "pop3s",
    3306: "mysql", 3389: "rdp", 5432: "postgresql", 6379: "redis",
    8080: "http", 8443: "https",
}
TLS_LIKELY_PORTS = {443, 465, 993, 995, 8443}

ConnectFunc = Callable[[str, int, float], tuple[socket.socket, bytes | None]]


def _default_connect(host: str, port: int, timeout: float):
    """Real TCP connect with optional TLS wrap for TLS-likely ports.

    Returns (socket, peer_cert_der_or_None). Raises TimeoutError on timeout,
    ConnectionRefusedError on refusal, OSError for the rest.
    """
    raw = socket.create_connection((host, port), timeout=timeout)
    if port in TLS_LIKELY_PORTS:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # observation only, no trust decisions
        tls = context.wrap_socket(raw, server_hostname=host)
        try:
            cert = tls.getpeercert(binary_form=True)
        except (ValueError, ssl.SSLError):
            cert = None
        return tls, cert
    return raw, None


class SafePortProbeClient:
    """Real known-port verification client (plan R6.2).

    One connection per explicitly authorized (host, port) pair - there is no
    scan primitive here at all. Statuses: open / closed / timeout / filtered /
    tls_error. Service names are port-table hints with bounded confidence; a
    TLS peer-certificate digest is recorded when a TLS handshake succeeds.
    """

    source_name = "safe_port"

    def __init__(self, timeout: float = 5.0, connect: ConnectFunc | None = None):
        self._timeout = timeout
        self._connect = connect if connect is not None else _default_connect

    async def health(self) -> dict[str, Any]:
        return {"ok": True, "timeout": self._timeout, "adapter": "safe_port"}

    async def probe(self, host: str, port: int) -> dict[str, Any]:
        started = time.perf_counter()
        host = host.strip().lower().rstrip(".")
        sock = None
        peer = None
        try:
            sock, peer = await asyncio.to_thread(self._connect, host, int(port), self._timeout)
        except TimeoutError:
            return self._outcome(host, port, "timeout", started)
        except ConnectionRefusedError:
            return self._outcome(host, port, "closed", started)
        except ssl.SSLError:
            # TLS-likely port answered but the handshake failed: still open
            return self._outcome(host, port, "open", started, tls_error=True)
        except OSError as exc:
            return self._outcome(host, port, "filtered", started, error_type=type(exc).__name__)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        return self._outcome(
            host, port, "open", started,
            tls_sha256=hashlib.sha256(peer).hexdigest() if peer else None,
        )

    @staticmethod
    def _outcome(host: str, port: int, status: str, started: float, *,
                 tls_sha256: str | None = None, tls_error: bool = False,
                 error_type: str | None = None) -> dict[str, Any]:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "status": status,
            "host": host,
            "port": int(port),
            "service_hint": PORT_HINTS.get(int(port)),
            "service_confidence": 0.5 if PORT_HINTS.get(int(port)) else 0.0,
            "tls_sha256": tls_sha256,
            "tls_error": tls_error,
            "error_type": error_type,
            "probe_ms": elapsed_ms,
            "queried_at": datetime.now(timezone.utc).isoformat(),
        }

