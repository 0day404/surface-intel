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

ConnectFunc = Callable[[str, int, float],
                        tuple[socket.socket, bytes | None, str | None, bool]]


def _default_connect(host: str, port: int, timeout: float):
    """Real TCP connect with optional TLS wrap for TLS-likely ports.

    Returns (socket, peer_cert_der_or_None, banner_or_None,
    closed_after_accept). Raises TimeoutError on connect timeout,
    ConnectionRefusedError on refusal, OSError for the rest. On plaintext
    (non-TLS) ports one passive recv() captures whatever the service pushes
    first (SSH/SMTP/FTP/MySQL greetings); a read timeout there is itself the
    finding - TCP up, service mute (the 8601 shape) - so banner stays None
    and the probe reports open_no_banner. A clean EOF (recv() == b"") sets
    closed_after_accept: the peer accepted and immediately dropped - the
    tarpit/hardening shape - which the probe reports via error_type so it is
    distinguishable from a silent hold. TLS ports never get the passive
    read: the handshake's peer certificate is the evidence.
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
        return tls, cert, None, False
    banner = None
    closed_after_accept = False
    try:
        raw.settimeout(min(timeout, 4.0))
        data = raw.recv(256)
        if data:
            banner = data.decode("utf-8", errors="replace").strip()[:160] or None
        else:
            # clean EOF: accepted, then closed with zero bytes pushed
            closed_after_accept = True
    except (TimeoutError, OSError):
        banner = None  # open but silent: the service never pushed a greeting
    return raw, None, banner, closed_after_accept


class SafePortProbeClient:
    """Real known-port verification client (plan R6.2).

    One connection per explicitly authorized (host, port) pair - there is no
    scan primitive here at all. Statuses: open / open_no_banner / closed /
    timeout / filtered / tls_error. open_no_banner means TCP accepted but the
    service pushed no greeting within the read window - "TCP up, service mute".
    Service names are port-table hints with bounded confidence; a TLS peer
    certificate digest is recorded when a TLS handshake succeeds, a banner
    snippet when a plaintext service greets first.
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
        banner = None
        closed_after_accept = False
        try:
            sock, peer, banner, closed_after_accept = await asyncio.to_thread(
                self._connect, host, int(port), self._timeout)
        except TimeoutError:
            return self._outcome(host, port, "timeout", started)
        except ConnectionRefusedError:
            return self._outcome(host, port, "closed", started)
        except ssl.SSLError as exc:
            # TLS-likely port answered but the handshake failed: still open.
            # error_type keeps the concrete handshake failure for remote triage
            return self._outcome(host, port, "open", started, tls_error=True,
                                 error_type=type(exc).__name__)
        except OSError as exc:
            return self._outcome(host, port, "filtered", started, error_type=type(exc).__name__)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        # A TLS peer cert or a banner proves an application answered; a bare
        # accept with neither is the silent-service shape (8601), so it gets
        # its own status instead of blending into "open". Within that status,
        # error_type separates a clean zero-byte EOF (tarpit/hardening: the
        # R3官网 3306 shape) from a silent hold (8601 shape).
        status = "open" if (peer is not None or banner is not None) else "open_no_banner"
        return self._outcome(
            host, port, status, started,
            tls_sha256=hashlib.sha256(peer).hexdigest() if peer else None,
            banner=banner,
            error_type="closed_after_accept" if closed_after_accept else None,
        )

    @staticmethod
    def _outcome(host: str, port: int, status: str, started: float, *,
                 tls_sha256: str | None = None, tls_error: bool = False,
                 error_type: str | None = None,
                 banner: str | None = None) -> dict[str, Any]:
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
            "banner": banner,
            "probe_ms": elapsed_ms,
            "queried_at": datetime.now(timezone.utc).isoformat(),
        }

