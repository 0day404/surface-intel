"""Controlled scope amendment (A1/A2).

One code path serves both callers - the CLI `scope-amend` command and the
recon `--auto-amend-discovered-ips` switch - so the snapshot write, the task
pointer update and the `scope_amended` audit event always land in the same
transaction with the same field set. Fail-closed rules:

- values must already be candidate assets of the task unless they arrive
  through the explicit user-confirmation channel (`--confirm-new-domain`),
  which is recorded as such in the audit event;
- active authorization only ever rises via the explicit `allow_active` flag,
  and only to safe_discovery/safe_probe - intrusive_test is unreachable by
  construction;
- everything that changed rides the audit event, including the source of the
  amendment ("manual" vs "auto_recon_dns_a").
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from ..states import ActiveLevel
from ..state.database import Database


class AmendmentError(ValueError):
    """Machine-readable refusal; the CLI maps code+detail to its exit output."""

    def __init__(self, code: str, detail: Any = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AmendmentValues:
    add_ips: tuple[str, ...]
    add_domains: tuple[str, ...]
    user_confirmed_new_domains: tuple[str, ...]

    def empty(self) -> bool:
        return not (self.add_ips or self.add_domains or self.user_confirmed_new_domains)


@dataclass(frozen=True)
class AmendOutcome:
    new_scope_version: str
    scope_id: str
    payload: dict[str, Any]
    added_ips: tuple[str, ...]
    added_domains: tuple[str, ...]
    user_confirmed_new_domains: tuple[str, ...]
    active_enabled: bool
    active_level: str
    added_ports: tuple[int, ...]
    frozen_actions: list[str]
    event_payload: dict[str, Any]


def resolve_amendment_values(
    db: Database,
    task_id: str,
    *,
    add_ips: tuple[str, ...] = (),
    add_domains: tuple[str, ...] = (),
    confirm_new_domain: bool = False,
) -> AmendmentValues:
    """Split requested values into candidate-eligible and user-confirmed-new.

    Raises AmendmentError("values_not_discovered_by_this_task") when values
    the task never discovered arrive without the explicit user-confirmation
    channel. An all-empty request is not an error here - an active-only
    amendment is legitimate; callers decide via AmendmentValues.empty().
    """
    ips = tuple(dict.fromkeys(item.strip() for item in add_ips if item.strip()))
    domains = tuple(dict.fromkeys(
        item.strip().rstrip(".") for item in add_domains if item.strip()))
    if not ips and not domains:
        return AmendmentValues((), (), ())
    eligible = {
        (row["asset_type"], row["normalized_value"])
        for row in db.rows(
            "SELECT asset_type, normalized_value FROM asset WHERE task_id=? AND scope_status='candidate'",
            (task_id,),
        )
    }
    unknown = [value for value in ips if ("ip", value.lower()) not in eligible] + \
              [value for value in domains if ("domain", value.lower()) not in eligible]
    if unknown and not confirm_new_domain:
        raise AmendmentError("values_not_discovered_by_this_task", detail=unknown)
    return AmendmentValues(
        add_ips=tuple(value for value in ips if value not in unknown),
        add_domains=tuple(value for value in domains if value not in unknown),
        user_confirmed_new_domains=tuple(unknown),
    )


def apply_scope_amendment(
    db: Database,
    task_id: str,
    *,
    base_payload: dict[str, Any],
    add_ips: tuple[str, ...] = (),
    add_domains: tuple[str, ...] = (),
    user_confirmed_new_domains: tuple[str, ...] = (),
    allow_active: bool = False,
    active_level: str = "safe_discovery",
    ports: tuple[int, ...] = (),
    reason: str = "",
    source: str = "manual",
    event_extra: dict[str, Any] | None = None,
) -> AmendOutcome:
    """Write the amended snapshot, task pointer and audit event atomically.

    The active level is fail-closed here as a second gate: anything outside
    {safe_discovery, safe_probe} raises instead of being coerced - the silent
    downgrade to passive_only is exactly the trap this module exists to avoid.
    """
    if allow_active and active_level not in {
        ActiveLevel.SAFE_DISCOVERY.value, ActiveLevel.SAFE_PROBE.value,
    }:
        raise AmendmentError("invalid_active_level", detail=active_level)

    payload = dict(base_payload)
    base_version = str(payload.get("scope_version", "scope-v1"))
    new_version = f"{base_version}-amend-{uuid.uuid4().hex[:6]}"
    payload["authorized_ips"] = sorted(set(payload.get("authorized_ips", [])) | set(add_ips))
    payload["authorized_domains"] = sorted(
        set(payload.get("authorized_domains", [])) | set(add_domains) | set(user_confirmed_new_domains))
    allowed_ports: set[int] = set()
    for item in payload.get("allowed_ports", []) or []:
        try:
            allowed_ports.add(int(item))
        except (TypeError, ValueError):
            continue
    allowed_ports.update(int(item) for item in ports)
    payload["allowed_ports"] = sorted(allowed_ports)
    if allow_active:
        payload["active_testing_allowed"] = True
        payload["active_testing_level"] = active_level
    payload["scope_version"] = new_version

    event_payload = {
        "task_id": task_id,
        "base_scope_version": base_version,
        "new_scope_version": new_version,
        "add_ips": list(add_ips),
        "add_domains": list(add_domains),
        "user_confirmed_new_domains": list(user_confirmed_new_domains),
        "user_confirmed_new": bool(user_confirmed_new_domains),
        "active_testing_allowed": bool(payload.get("active_testing_allowed")),
        "active_testing_level": payload.get("active_testing_level"),
        "added_ports": sorted({int(item) for item in ports}),
        "reason": reason,
        "source": source,
    }
    if event_extra:
        event_payload.update(event_extra)

    scope_id = f"scope-{uuid.uuid4().hex[:16]}"
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO scope_snapshot(scope_id,task_id,scope_version,payload_json,confirmed)
               VALUES(?,?,?,?,1)""",
            (scope_id, task_id, new_version, json.dumps(payload, ensure_ascii=True)),
        )
        connection.execute(
            "UPDATE task SET scope_version=?, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
            (new_version, task_id),
        )
        connection.execute(
            "INSERT INTO event(event_id,task_id,event_type,payload_json) VALUES(?,?,?,?)",
            (f"event-{uuid.uuid4().hex[:16]}", task_id, "scope_amended",
             json.dumps(event_payload, ensure_ascii=True)),
        )

    from ..state.recovery import freeze_stale_actions

    frozen = freeze_stale_actions(db, task_id, new_version)
    return AmendOutcome(
        new_scope_version=new_version,
        scope_id=scope_id,
        payload=payload,
        added_ips=tuple(add_ips),
        added_domains=tuple(add_domains),
        user_confirmed_new_domains=tuple(user_confirmed_new_domains),
        active_enabled=bool(allow_active),
        active_level=active_level if allow_active else "",
        added_ports=tuple(sorted({int(item) for item in ports})),
        frozen_actions=frozen,
        event_payload=event_payload,
    )
