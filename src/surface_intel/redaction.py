from __future__ import annotations

import re
from typing import Any

_SENSITIVE = re.compile(
    r"(authorization|cookie|set-cookie|token|password|passwd|secret|api[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)


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
    if isinstance(value, str) and _SENSITIVE.search(value):
        return "[REDACTED]"
    return value
