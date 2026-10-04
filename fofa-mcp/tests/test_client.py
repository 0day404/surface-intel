from __future__ import annotations

import json
import httpx
import pytest

from fofa_mcp.accounts import AccountManager
from fofa_mcp.client import FofaClient, _backoff_delay, _clock, _sleep
from fofa_mcp.config import AccountConfig, Settings
from fofa_mcp.errors import (
    NetworkError,
    QuotaExceededError,
    RateLimitError,
    ResponseTooLargeError,
    ResponseParseError,
)


class _FakeResponse:
    status_code = 200
    content = b"{}"
    headers: dict = {}

    def json(self):
        return {"results": [["1.2.3.4", "443"]], "consumed_fpoint": 1}


class _FakeAsyncClient:
    calls: list[str] = []
    urls: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, *, params, headers):
        self.urls.append(url)
        label = "primary" if params["key"] == "primary-key" else "backup"
        self.calls.append(label)
        if label == "primary":
            raise httpx.ConnectTimeout("primary unavailable")
        return _FakeResponse()


@pytest.mark.asyncio
async def test_network_failure_switches_to_backup_account(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr("fofa_mcp.client.httpx.AsyncClient", _FakeAsyncClient)
    settings = Settings(
        api_base="https://fofa.test",
        timeout_sec=1,
        default_page_size=100,
        max_page_size=200,
        max_cursor_pages=10,
        max_requests_per_session=20,
        max_estimated_fpoints=100,
        max_total_results=1000,
        max_query_length=4096,
        log_level="WARNING",
        max_retries=0,
        max_host_response_bytes=2_000_000,
        accounts=(
            AccountConfig("primary", "", "primary-key"),
            AccountConfig("backup", "", "backup-key"),
        ),
    )
    client = FofaClient(settings, AccountManager(settings.accounts))

    result = await client.search("domain=\"example.com\"", ["ip"], 1, 1)

    assert result["_account_used"] == "backup"
    assert _FakeAsyncClient.calls == ["primary", "backup"]


@pytest.mark.asyncio
async def test_host_ipv6_path_is_encoded(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.urls = []
    class _HostClient(_FakeAsyncClient):
        async def get(self, url, *, params, headers):
            self.urls.append(url)
            return _FakeResponse()

    monkeypatch.setattr("fofa_mcp.client.httpx.AsyncClient", _HostClient)
    settings = Settings(
        api_base="https://fofa.test",
        timeout_sec=1,
        default_page_size=100,
        max_page_size=200,
        max_cursor_pages=10,
        max_requests_per_session=20,
        max_estimated_fpoints=100,
        max_total_results=1000,
        max_query_length=4096,
        max_retries=0,
        max_host_response_bytes=2_000_000,
        log_level="WARNING",
        accounts=(AccountConfig("primary", "", "primary-key"),),
    )
    client = FofaClient(settings, AccountManager(settings.accounts))

    await client.host("2001:db8::1", detail=False)

    assert _FakeAsyncClient.urls == ["https://fofa.test/host/2001%3Adb8%3A%3A1"]


@pytest.mark.asyncio
async def test_host_response_size_limit_is_explicit(monkeypatch):
    class _LargeResponse(_FakeResponse):
        content = b"x" * 10

    class _LargeClient(_FakeAsyncClient):
        async def get(self, url, *, params, headers):
            return _LargeResponse()

    monkeypatch.setattr("fofa_mcp.client.httpx.AsyncClient", _LargeClient)
    settings = Settings(
        api_base="https://fofa.test",
        timeout_sec=1,
        default_page_size=100,
        max_page_size=200,
        max_cursor_pages=10,
        max_requests_per_session=20,
        max_estimated_fpoints=100,
        max_total_results=1000,
        max_query_length=4096,
        max_retries=0,
        max_host_response_bytes=5,
        log_level="WARNING",
        accounts=(AccountConfig("primary", "", "primary-key"),),
    )
    client = FofaClient(settings, AccountManager(settings.accounts))

    with pytest.raises(ResponseTooLargeError):
        await client.host("example.com", detail=True)


@pytest.mark.asyncio
async def test_malformed_json_has_stable_response_error(monkeypatch):
    class _MalformedResponse(_FakeResponse):
        def json(self):
            raise json.JSONDecodeError("malformed", "", 0)

    class _MalformedClient(_FakeAsyncClient):
        async def get(self, url, *, params, headers):
            return _MalformedResponse()

    monkeypatch.setattr("fofa_mcp.client.httpx.AsyncClient", _MalformedClient)
    settings = Settings(
        api_base="https://fofa.test",
        timeout_sec=1,
        default_page_size=100,
        max_page_size=200,
        max_cursor_pages=10,
        max_requests_per_session=20,
        max_estimated_fpoints=100,
        max_total_results=1000,
        max_query_length=4096,
        max_retries=0,
        max_host_response_bytes=2_000_000,
        log_level="WARNING",
        accounts=(AccountConfig("primary", "", "primary-key"),),
    )
    client = FofaClient(settings, AccountManager(settings.accounts))

    with pytest.raises(ResponseParseError):
        await client.search("domain=\"example.com\"", ["ip"], 1, 1)


def _settings(accounts, **overrides):
    defaults = dict(
        api_base="https://fofa.test",
        timeout_sec=1,
        default_page_size=100,
        max_page_size=200,
        max_cursor_pages=10,
        max_requests_per_session=20,
        max_estimated_fpoints=100,
        max_total_results=1000,
        max_query_length=4096,
        log_level="WARNING",
        max_retries=0,
        max_host_response_bytes=2_000_000,
        accounts=accounts,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class _ErrorResponse(_FakeResponse):
    def __init__(self, errmsg, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._errmsg = errmsg

    def json(self):
        return {"error": True, "errmsg": self._errmsg}


class _ScriptedAsyncClient:
    """按 (账号标签, 调用序号) 出牌的假 httpx 客户端。"""

    def __init__(self, script):
        self.script = script
        self.calls: list[str] = []

    async def get(self, url, *, params, headers):
        label = params["key"].removesuffix("-key")
        index = self.calls.count(label)
        self.calls.append(label)
        return self.script[(label, index)]


class _RecordingSleeper:
    def __init__(self):
        self.sleeps: list[float] = []

    async def __call__(self, seconds):
        self.sleeps.append(seconds)


def test_backoff_delay_pure_function():
    assert [_backoff_delay(attempt, None, 0.5) for attempt in range(3)] == [0.5, 1.0, 2.0]
    assert _backoff_delay(10, None, 0.5) == 30.0
    assert _backoff_delay(0, "7", 0.5) == 7.0
    assert _backoff_delay(0, "abc", 0.5) == 0.5
    assert _backoff_delay(0, "-3", 0.5) == 0.5


@pytest.mark.asyncio
async def test_all_accounts_quota_error_raises_real_cause(monkeypatch):
    fake = _ScriptedAsyncClient(
        {
            ("primary", 0): _ErrorResponse("F点不足"),
            ("backup", 0): _ErrorResponse("F点不足"),
        }
    )
    monkeypatch.setattr("fofa_mcp.client.httpx.AsyncClient", lambda *a, **k: fake)
    settings = _settings(
        (
            AccountConfig("primary", "", "primary-key"),
            AccountConfig("backup", "", "backup-key"),
        )
    )
    client = FofaClient(settings, AccountManager(settings.accounts))

    with pytest.raises(QuotaExceededError):
        await client.search('domain="example.com"', ["ip"], 1, 1)
    assert fake.calls == ["primary", "backup"]


@pytest.mark.asyncio
async def test_all_accounts_rate_limited_raises_rate_limit(monkeypatch):
    fake = _ScriptedAsyncClient(
        {
            ("primary", 0): _ErrorResponse("too many requests", status_code=429),
            ("backup", 0): _ErrorResponse("too many requests", status_code=429),
        }
    )
    monkeypatch.setattr("fofa_mcp.client.httpx.AsyncClient", lambda *a, **k: fake)
    settings = _settings(
        (
            AccountConfig("primary", "", "primary-key"),
            AccountConfig("backup", "", "backup-key"),
        )
    )
    client = FofaClient(settings, AccountManager(settings.accounts))

    with pytest.raises(RateLimitError):
        await client.search('domain="example.com"', ["ip"], 1, 1)


@pytest.mark.asyncio
async def test_backoff_sleeps_between_retries_then_switches(monkeypatch):
    fake = _ScriptedAsyncClient(
        {
            ("primary", 0): _ErrorResponse("too many requests", status_code=429),
            ("primary", 1): _ErrorResponse("too many requests", status_code=429),
            ("primary", 2): _ErrorResponse("too many requests", status_code=429),
            ("backup", 0): _FakeResponse(),
        }
    )
    monkeypatch.setattr("fofa_mcp.client.httpx.AsyncClient", lambda *a, **k: fake)
    sleeper = _RecordingSleeper()
    monkeypatch.setattr("fofa_mcp.client._sleep", sleeper)
    settings = _settings(
        (
            AccountConfig("primary", "", "primary-key"),
            AccountConfig("backup", "", "backup-key"),
        ),
        max_retries=2,
        retry_backoff_sec=0.5,
    )
    client = FofaClient(settings, AccountManager(settings.accounts))

    result = await client.search('domain="example.com"', ["ip"], 1, 1)

    assert result["_account_used"] == "backup"
    assert sleeper.sleeps == [0.5, 1.0]


@pytest.mark.asyncio
async def test_retry_after_header_is_respected(monkeypatch):
    fake = _ScriptedAsyncClient(
        {
            ("primary", 0): _ErrorResponse(
                "too many requests", status_code=429, headers={"Retry-After": "7"}
            ),
            ("primary", 1): _FakeResponse(),
            ("backup", 0): _FakeResponse(),
        }
    )
    monkeypatch.setattr("fofa_mcp.client.httpx.AsyncClient", lambda *a, **k: fake)
    sleeper = _RecordingSleeper()
    monkeypatch.setattr("fofa_mcp.client._sleep", sleeper)
    settings = _settings(
        (
            AccountConfig("primary", "", "primary-key"),
            AccountConfig("backup", "", "backup-key"),
        ),
        max_retries=1,
    )
    client = FofaClient(settings, AccountManager(settings.accounts))

    await client.search('domain="example.com"', ["ip"], 1, 1)

    assert sleeper.sleeps == [7.0]


@pytest.mark.asyncio
async def test_call_deadline_stops_before_second_account(monkeypatch):
    """时限在第二账号前耗尽时，抛的是第一个账号的真实错误（D2 语义）。"""
    fake = _ScriptedAsyncClient(
        {
            ("primary", 0): _ErrorResponse("too many requests", status_code=429),
            ("backup", 0): _FakeResponse(),
        }
    )
    monkeypatch.setattr("fofa_mcp.client.httpx.AsyncClient", lambda *a, **k: fake)
    clock_values = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr("fofa_mcp.client._clock", lambda: next(clock_values))
    settings = _settings(
        (
            AccountConfig("primary", "", "primary-key"),
            AccountConfig("backup", "", "backup-key"),
        ),
        max_call_seconds=5.0,
    )
    client = FofaClient(settings, AccountManager(settings.accounts))

    with pytest.raises(RateLimitError):
        await client.search('domain="example.com"', ["ip"], 1, 1)
    assert fake.calls == ["primary"]


@pytest.mark.asyncio
async def test_call_deadline_exceeded_before_first_account(monkeypatch):
    fake = _ScriptedAsyncClient(
        {
            ("primary", 0): _FakeResponse(),
        }
    )
    monkeypatch.setattr("fofa_mcp.client.httpx.AsyncClient", lambda *a, **k: fake)
    clock_values = iter([0.0, 100.0])
    monkeypatch.setattr("fofa_mcp.client._clock", lambda: next(clock_values))
    settings = _settings(
        (AccountConfig("primary", "", "primary-key"),),
        max_call_seconds=5.0,
    )
    client = FofaClient(settings, AccountManager(settings.accounts))

    with pytest.raises(NetworkError, match="deadline"):
        await client.search('domain="example.com"', ["ip"], 1, 1)
    assert fake.calls == []
