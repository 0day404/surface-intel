from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import dns.asyncresolver
import dns.exception
import dns.flags
import dns.resolver

from .adapters.dns import DnsResolverClient


class DnspythonResolverClient(DnsResolverClient):
    """Real DNS client over dnspython.

    Absolute names are queried (trailing dot) so resolver search lists can
    never silently expand the target. The `dnssec` field only reports the
    upstream AD bit; no local validation is performed.
    """

    def __init__(
        self,
        timeout: float = 5.0,
        nameservers: list[str] | None = None,
        resolver: dns.asyncresolver.Resolver | None = None,
    ):
        self._resolver = resolver if resolver is not None else dns.asyncresolver.Resolver(configure=True)
        self._resolver.timeout = timeout
        self._resolver.lifetime = timeout
        self._resolver.edns = 0
        self._resolver.payload = 1232
        if nameservers:
            self._resolver.nameservers = list(nameservers)

    @property
    def _resolver_label(self) -> str:
        return ",".join(str(item) for item in self._resolver.nameservers) or "system"

    async def health(self) -> dict[str, Any]:
        return {
            "ok": bool(self._resolver.nameservers),
            "resolvers": [str(item) for item in self._resolver.nameservers],
            "timeout": self._resolver.timeout,
            "adapter": "dns_resolver",
        }

    async def resolve(self, name: str, record_type: str) -> dict[str, Any]:
        normalized = name.strip().rstrip(".").lower()
        started = time.perf_counter()
        base = {
            "name": normalized,
            "record_type": record_type.upper(),
            "resolver": self._resolver_label,
        }
        try:
            answer = await self._resolver.resolve(f"{normalized}.", record_type.upper())
        except dns.resolver.NXDOMAIN:
            return {**base, "status": "nxdomain", "records": [], "resolved_at": _now()}
        except dns.resolver.NoAnswer:
            return {**base, "status": "empty", "records": [], "resolved_at": _now()}
        except dns.resolver.NoNameservers:
            return {**base, "status": "servfail", "records": [], "resolved_at": _now()}
        except dns.exception.Timeout:
            return {**base, "status": "timeout", "records": [], "resolved_at": _now()}
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            **base,
            "status": "observed",
            "records": _format_records(answer, record_type.upper()),
            "ttl": getattr(getattr(answer, "rrset", None), "ttl", None),
            "resolved_at": _now(),
            "resolution_ms": elapsed_ms,
            "dnssec": _ad_bit(answer),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ad_bit(answer: Any) -> bool | None:
    response = getattr(answer, "response", None)
    flags_value = getattr(response, "flags", None)
    if flags_value is None:
        return None
    return bool(flags_value & dns.flags.AD)


def _format_records(answer: Any, record_type: str) -> list[str]:
    records: list[str] = []
    for rdata in answer:
        try:
            if record_type in {"A", "AAAA"}:
                records.append(str(rdata.address))
            elif record_type in {"CNAME", "NS"}:
                records.append(str(rdata.target).rstrip("."))
            elif record_type == "MX":
                records.append(f"{rdata.preference} {str(rdata.exchange).rstrip('.')}")
            elif record_type == "TXT":
                records.append(b"".join(rdata.strings).decode("utf-8", "replace"))
            elif record_type == "SOA":
                records.append(
                    f"{str(rdata.mname).rstrip('.')} {str(rdata.rname).rstrip('.')} {rdata.serial}"
                )
            else:
                records.append(str(rdata))
        except AttributeError:
            records.append(str(rdata))
    return records


def configured_dns_client(
    timeout: float = 5.0,
    nameservers: list[str] | None = None,
) -> DnspythonResolverClient:
    """Build the real resolver client; nameservers default to env override then system."""
    if nameservers is None:
        env_value = os.environ.get("SURFACE_INTEL_DNS_NAMESERVERS", "").strip()
        nameservers = [item.strip() for item in env_value.split(",") if item.strip()] or None
    return DnspythonResolverClient(timeout=timeout, nameservers=nameservers)
