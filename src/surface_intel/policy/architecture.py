from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeDecision:
    architecture_status: str
    testing_allowed: bool
    allowed_actions: tuple[str, ...]
    blocked_actions: tuple[str, ...]
    review_required: bool
    reason: str


def architecture_probe_decision(
    *,
    architecture_status: str,
    scope_confirmed: bool,
    active_level: str,
    evidence_refs: tuple[str, ...] = (),
) -> ProbeDecision:
    blocked = ("full_port_scan", "credential_testing", "exploitation", "dos_stress")
    allowed = ("http_observation", "web_fingerprint")
    if not scope_confirmed:
        return ProbeDecision(architecture_status, False, (), blocked, True, "scope_not_confirmed")
    if architecture_status in {"third_party", "origin_candidate", "unknown"}:
        return ProbeDecision(architecture_status, False, (), blocked, True, "architecture_requires_review")
    if architecture_status == "shared_infrastructure":
        return ProbeDecision(architecture_status, active_level in {"safe_discovery", "safe_probe"}, allowed, blocked, True, "shared_entrypoint_only")
    if architecture_status in {"confirmed_direct", "confirmed_hosted"} and active_level in {"safe_discovery", "safe_probe"}:
        return ProbeDecision(architecture_status, True, allowed, blocked, False, "authorized_low_impact_probe")
    return ProbeDecision(architecture_status, False, (), blocked, True, "active_level_insufficient")


def is_architecture_safe_for_probe(decision: ProbeDecision) -> bool:
    return decision.testing_allowed and not decision.review_required
