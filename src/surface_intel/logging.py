from __future__ import annotations

import json
import logging
from typing import Any

from .redaction import redact


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        return json.dumps(redact(payload), ensure_ascii=True)


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("surface_intel")
    logger.setLevel(level.upper())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(RedactingFormatter())
        logger.addHandler(handler)
    return logger
