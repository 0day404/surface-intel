from __future__ import annotations

import hashlib
import re
from typing import Any

_SENSITIVE = re.compile(
    r"(authorization|cookie|set-cookie|token|password|passwd|secret|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)

# R3 lesson: redact()'s string branch used the raw _SENSITIVE vocabulary, so
# a MySQL handshake banner got nuked wholesale because the wire protocol
# names its auth plugin "mysql_native_password". Value-level redaction only
# fires on credential-assignment shapes ("password=hunter2", JSON
# '"token": "..."', "authorization: Bearer ...", "?token=" query strings);
# protocol vocabulary without an assignment separator stays readable.
_VALUE_CREDENTIAL = re.compile(
    r"[\"']?(authorization|set-cookie|cookie|password|passwd|secret|token|api[_-]?key|private[_-]?key)[\"']?\s*[:=]",
    re.IGNORECASE,
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SENSITIVE.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str) and _VALUE_CREDENTIAL.search(value):
        return "[REDACTED]"
    return value


def email_placeholder(task_id: str, email: str) -> str:
    """Deterministic per-task placeholder - same address maps to the same
    token within a task, different tasks never share one."""
    return "{{EMAIL_" + hashlib.sha256(f"{task_id}:{email}".encode()).hexdigest()[:8] + "}}"


def placeholderize(value: Any, task_id: str) -> tuple[Any, list[tuple[str, str]]]:
    """Replace email addresses with stable per-task placeholders (C3).

    Returns (new_value, [(placeholder, plaintext), ...]). The caller persists
    the pairs into the local redaction_map table so `report --reveal-emails`
    can restore them; deliverables default to placeholders only. Upstream
    tokens like support@cnispgroup.com contain no '@' and pass through untouched.
    """
    pairs: list[tuple[str, str]] = []

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: walk(item) for key, item in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, tuple):
            return tuple(walk(item) for item in node)
        if isinstance(node, str) and "@" in node:
            def substitute(match: re.Match[str]) -> str:
                email = match.group(0)
                placeholder = email_placeholder(task_id, email)
                pairs.append((placeholder, email))
                return placeholder

            return EMAIL_RE.sub(substitute, node)
        return node

    return walk(value), pairs
