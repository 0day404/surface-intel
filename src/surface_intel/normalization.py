from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class NormalizedTarget:
    raw_value: str
    target_type: str
    normalized_value: str
    source: str = "user"
    status: str = "normalized"


def normalize_target(raw_value: str, source: str = "user") -> NormalizedTarget:
    raw = raw_value.strip()
    try:
        return NormalizedTarget(raw, "ip", str(ipaddress.ip_address(raw)), source)
    except ValueError:
        pass
    try:
        return NormalizedTarget(raw, "cidr", str(ipaddress.ip_network(raw, strict=False)), source)
    except ValueError:
        pass
    if "://" in raw:
        parsed = urlsplit(raw)
        host = parsed.hostname or ""
        normalized_host = host.encode("idna").decode("ascii").lower().rstrip(".")
        port = parsed.port
        netloc = normalized_host if port is None else f"{normalized_host}:{port}"
        return NormalizedTarget(raw, "url", urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, "")), source)
    normalized = raw.encode("idna").decode("ascii").lower().rstrip(".")
    return NormalizedTarget(raw, "domain", normalized, source)


def normalize_targets(values: list[str], source: str = "user") -> list[dict[str, str]]:
    unique: dict[tuple[str, str], NormalizedTarget] = {}
    for value in values:
        item = normalize_target(value, source)
        unique[(item.target_type, item.normalized_value)] = item
    return [asdict(item) for item in unique.values()]
