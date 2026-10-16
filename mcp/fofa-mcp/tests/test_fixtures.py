from __future__ import annotations

import json
from pathlib import Path

import pytest

from fofa_mcp.accounts import classify_api_error
from fofa_mcp.errors import (
    AuthenticationError,
    InvalidFieldError,
    QuotaExceededError,
    RateLimitError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "fofa"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_success_and_empty_fixtures_are_valid():
    success = load_fixture("search_success.json")
    empty = load_fixture("search_empty.json")
    assert len(success["results"]) == 2
    assert empty["results"] == []


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("error_rate_limit.json", RateLimitError),
        ("error_authentication.json", AuthenticationError),
        ("error_quota.json", QuotaExceededError),
    ],
)
def test_error_fixtures_map_to_stable_types(fixture, expected):
    payload = load_fixture(fixture)
    error = classify_api_error(payload["errmsg"], None, "primary")
    assert isinstance(error, expected)
    assert isinstance(error.retryable, bool)


def test_field_error_fixture_is_not_silent():
    payload = load_fixture("error_field.json")
    error = classify_api_error(payload["errmsg"], 400, "primary")
    assert error.error_type in {"remote_api_error", "invalid_field"}
