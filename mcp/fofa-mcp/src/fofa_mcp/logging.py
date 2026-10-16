from __future__ import annotations

import logging
import re
import sys


_SENSITIVE_PATTERNS = (
    (re.compile(r"(?i)([?&](?:key|token|api_key|apikey|password|secret)=)[^&\s]+"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)(\b(?:key|token|api_key|apikey|password|secret|authorization)\b"
            r"\s*[:=]\s*)(?:Bearer\s+)?[^\s,;]+"
        ),
        r"\1[REDACTED]",
    ),
)


def redact_log_text(value: str) -> str:
    redacted = value
    for pattern, replacement in _SENSITIVE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
        force=True,
    )
    for handler in logging.getLogger().handlers:
        handler.setFormatter(
            _RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    # HTTPX logs full query URLs at INFO, which would expose FOFA credentials.
    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
