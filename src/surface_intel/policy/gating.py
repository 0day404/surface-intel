from __future__ import annotations

from .scope import Scope


def is_target_in_scope(scope: Scope, target: str) -> bool:
    return scope.is_in_scope(target)


def is_action_allowed(scope: Scope, target: str, active: bool = False) -> tuple[bool, str]:
    return scope.is_action_allowed(target=target, active=active)


def require_review(reason: str) -> dict[str, str]:
    return {"status": "needs_review", "reason": reason}
