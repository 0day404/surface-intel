from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Any

from ..states import ActiveLevel


def _normalized_asn(value: str) -> str | None:
    cleaned = str(value or "").strip().upper().removeprefix("AS")
    return cleaned if cleaned.isdigit() else None


@dataclass(frozen=True)
class Scope:
    authorized_targets: tuple[str, ...] = ()
    authorized_domains: tuple[str, ...] = ()
    authorized_ips: tuple[str, ...] = ()
    authorized_cidrs: tuple[str, ...] = ()
    authorized_asns: tuple[str, ...] = ()
    allowed_ports: tuple[int, ...] = ()
    excluded_targets: tuple[str, ...] = ()
    active_testing_allowed: bool = False
    active_testing_level: ActiveLevel = ActiveLevel.PASSIVE_ONLY
    confirmed: bool = False
    scope_version: str = "scope-v0001"
    starts_at: str | None = None
    ends_at: str | None = None

    def __post_init__(self):
        # fail-closed coercion: a string level from a scope document must
        # never compare unequal to the enum and slip past passive-only gates
        if isinstance(self.active_testing_level, str):
            try:
                object.__setattr__(self, "active_testing_level", ActiveLevel(self.active_testing_level))
            except ValueError:
                object.__setattr__(self, "active_testing_level", ActiveLevel.PASSIVE_ONLY)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scope":
        return cls(
            **{
                field: tuple(data.get(field, ())) if field not in {"active_testing_allowed", "active_testing_level", "confirmed", "scope_version", "starts_at", "ends_at"} else data.get(field, default)
                for field, default in (
                    ("authorized_targets", ()), ("authorized_domains", ()), ("authorized_ips", ()),
                    ("authorized_cidrs", ()), ("authorized_asns", ()), ("allowed_ports", ()),
                    ("excluded_targets", ()),
                    ("active_testing_allowed", False), ("active_testing_level", ActiveLevel.PASSIVE_ONLY),
                    ("confirmed", False), ("scope_version", "scope-v0001"), ("starts_at", None), ("ends_at", None),
                )
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "authorized_targets": list(self.authorized_targets),
            "authorized_domains": list(self.authorized_domains),
            "authorized_ips": list(self.authorized_ips),
            "authorized_cidrs": list(self.authorized_cidrs),
            "authorized_asns": list(self.authorized_asns),
            "allowed_ports": list(self.allowed_ports),
            "excluded_targets": list(self.excluded_targets),
            "active_testing_allowed": self.active_testing_allowed,
            "active_testing_level": self.active_testing_level.value
            if isinstance(self.active_testing_level, ActiveLevel) else str(self.active_testing_level),
            "confirmed": self.confirmed,
            "scope_version": self.scope_version,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
        }

    def is_in_scope(self, value: str) -> bool:
        candidate = value.strip().lower().rstrip(".")
        if any(fnmatch(candidate, item.lower().rstrip(".")) for item in self.excluded_targets):
            return False
        if candidate in {item.lower().rstrip(".") for item in self.authorized_targets}:
            return True
        if any(fnmatch(candidate, item.lower().rstrip(".")) for item in self.authorized_domains):
            return True
        candidate_asn = _normalized_asn(candidate)
        if candidate_asn is not None and candidate_asn in {
            _normalized_asn(item) for item in self.authorized_asns
        }:
            return True
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            address = None
        if address and any(address in ipaddress.ip_network(cidr) for cidr in self.authorized_cidrs):
            return True
        return candidate in {item.lower() for item in self.authorized_ips}

    def is_action_allowed(self, *, target: str, active: bool = False) -> tuple[bool, str]:
        if not self.confirmed:
            return False, "scope_not_confirmed"
        if not self.is_in_scope(target):
            return False, "target_out_of_scope"
        if active and (not self.active_testing_allowed or self.active_testing_level == ActiveLevel.PASSIVE_ONLY):
            return False, "active_testing_not_authorized"
        now = datetime.now(timezone.utc)
        if self.starts_at and now < datetime.fromisoformat(self.starts_at):
            return False, "outside_time_window"
        if self.ends_at and now > datetime.fromisoformat(self.ends_at):
            return False, "outside_time_window"
        return True, "allowed"
