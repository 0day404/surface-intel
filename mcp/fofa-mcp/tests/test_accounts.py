import pytest

from fofa_mcp.accounts import AccountManager, classify_api_error
from fofa_mcp.config import AccountConfig
from fofa_mcp.errors import (
    AuthenticationError,
    FofaError,
    QuotaExceededError,
    RateLimitError,
)


def test_rate_limited_account_is_deprioritized():
    manager = AccountManager(
        (
            AccountConfig("primary", "", "primary-key"),
            AccountConfig("backup", "", "backup-key"),
        ),
        cooldown_sec=300,
    )
    states = manager.available()
    manager.mark_failure(states[0], RateLimitError("limited"))
    assert [state.config.label for state in manager.available()] == ["backup", "primary"]


@pytest.mark.parametrize(
    ("message", "status", "expected"),
    [
        # F 点不足是配额耗尽而非限流：不可重试，立即停/换号（D2 核心回归）。
        ("F点不足", None, QuotaExceededError),
        ("fpoint 不足", None, QuotaExceededError),
        ("insufficient quota", None, QuotaExceededError),
        ("账户余额不足", None, QuotaExceededError),
        ("too many requests", 429, RateLimitError),
        ("820041", None, RateLimitError),
        ("[-1] error", None, AuthenticationError),
    ],
)
def test_classify_api_error_mapping(message, status, expected):
    error = classify_api_error(message, status, "primary")
    assert isinstance(error, expected)
    assert isinstance(error.retryable, bool)


def test_classify_unknown_message_is_non_retryable_fofa_error():
    error = classify_api_error("some totally new server message", 500, "primary")
    assert type(error) is FofaError
    assert error.retryable is False
    assert "some totally new server message" in str(error)
