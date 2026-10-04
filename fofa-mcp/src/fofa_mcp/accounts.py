from __future__ import annotations

import time
from dataclasses import dataclass

from .config import AccountConfig
from .errors import (
    AuthenticationError,
    FofaError,
    InvalidFieldError,
    QuotaExceededError,
    RateLimitError,
)


@dataclass
class AccountState:
    config: AccountConfig
    unavailable_until: float = 0
    last_error: str = ""
    last_success: float | None = None


class AccountManager:
    def __init__(self, accounts: tuple[AccountConfig, ...], cooldown_sec: int = 300):
        self._states = [AccountState(account) for account in accounts]
        self.cooldown_sec = cooldown_sec

    def available(self) -> list[AccountState]:
        now = time.monotonic()
        live = [state for state in self._states if state.unavailable_until <= now]
        cooling = [state for state in self._states if state.unavailable_until > now]
        return live + cooling

    def mark_success(self, state: AccountState) -> None:
        state.last_success = time.monotonic()
        state.last_error = ""

    def mark_failure(self, state: AccountState, error: Exception) -> None:
        state.last_error = str(error)
        if isinstance(error, (RateLimitError, QuotaExceededError)):
            state.unavailable_until = time.monotonic() + self.cooldown_sec

    def snapshot(self) -> list[dict[str, str | bool]]:
        now = time.monotonic()
        return [
            {
                "account": state.config.label,
                "email": self._redact_email(state.config.email),
                "available": state.unavailable_until <= now,
                "last_error": state.last_error,
            }
            for state in self._states
        ]

    @staticmethod
    def _redact_email(email: str) -> str:
        if not email or "@" not in email:
            return ""
        local, domain = email.split("@", 1)
        return f"{local[:1]}***@{domain}"


def classify_api_error(message: str, status_code: int | None, account: str) -> Exception:
    text = message.lower()
    if status_code in (401, 403) or any(token in text for token in ("unauthorized", "invalid key", "key error", "[-1]", "[-3]")):
        return AuthenticationError("FOFA authentication failed", account=account)
    # Quota/余额/F 点耗尽必须先于 rate limit 判定：它们不可重试，应立即停或换号，
    # 而 "f点不足" 此前被误判为可重试 rate_limit 导致空转重试。
    if any(token in text for token in ("quota", "上限", "余额不足", "insufficient", "f点", "fpoint")):
        return QuotaExceededError("FOFA quota was exceeded", account=account)
    if status_code == 429 or any(token in text for token in ("too many", "rate", "频繁", "820041")):
        return RateLimitError("FOFA request was rate limited", account=account)
    if any(token in text for token in ("invalid field", "unknown field", "field error")):
        return InvalidFieldError("FOFA field was rejected", account=account)
    # 未识别消息按不可重试的基类处理，保留真实 errmsg，不再丢进可重试桶。
    return FofaError(message, account=account)
