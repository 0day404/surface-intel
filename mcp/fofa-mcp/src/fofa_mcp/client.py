from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from urllib.parse import quote
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .accounts import AccountManager, AccountState, classify_api_error
from .config import Settings
from .errors import (
    FofaError,
    NetworkError,
    RemoteApiError,
    ResponseParseError,
    ResponseTooLargeError,
)
from .version import SERVER_VERSION

logger = logging.getLogger(__name__)

# 测试注入点：生产用真实时钟与异步睡眠。
_sleep = asyncio.sleep
_clock = time.monotonic

_MAX_BACKOFF_SEC = 30.0


def _backoff_delay(attempt: int, retry_after: str | None, base: float) -> float:
    """Retry-After（纯数字秒）优先，否则指数退避；上限 30s。"""
    if retry_after:
        try:
            value = float(retry_after.strip())
        except ValueError:
            value = -1.0
        if value > 0:
            return min(value, _MAX_BACKOFF_SEC)
    return min(base * (2**attempt), _MAX_BACKOFF_SEC)


class FofaClient:
    def __init__(self, settings: Settings, accounts: AccountManager):
        self.settings = settings
        self.accounts = accounts
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        # 连接池复用：生命周期跟随服务进程（stdio 进程退出时由 OS 兜底回收）。
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.timeout_sec)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        path: str,
        params_factory: Callable[[AccountState], dict[str, Any]],
    ) -> dict[str, Any]:
        tried: list[str] = []
        last_error: FofaError | None = None
        deadline = _clock() + self.settings.max_call_seconds
        client = await self._http()
        for state in self.accounts.available():
            if _clock() >= deadline:
                if last_error is None:
                    last_error = NetworkError(
                        "FOFA call deadline exceeded", account=state.config.label
                    )
                break
            tried.append(state.config.label)
            for attempt in range(self.settings.max_retries + 1):
                try:
                    response = await client.get(
                        f"{self.settings.api_base}/{path.lstrip('/')}",
                        params=params_factory(state),
                        headers={
                            "Accept": "application/json",
                            "User-Agent": f"fofa-mcp/{SERVER_VERSION}",
                        },
                    )
                    retry_after = response.headers.get("Retry-After")
                    if (
                        path.startswith("host/")
                        and len(response.content) > self.settings.max_host_response_bytes
                    ):
                        raise ResponseTooLargeError(
                            "FOFA host response exceeds the configured size limit",
                            account=state.config.label,
                        )
                    try:
                        data = response.json()
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise ResponseParseError(
                            "FOFA returned malformed JSON",
                            account=state.config.label,
                        ) from exc
                    if not isinstance(data, dict):
                        raise RemoteApiError("FOFA returned a non-object response", account=state.config.label)
                    if data.get("error") is True or response.status_code >= 400:
                        message = str(data.get("errmsg") or data.get("message") or f"HTTP {response.status_code}")
                        error = classify_api_error(message, response.status_code, state.config.label)
                        self.accounts.mark_failure(state, error)
                        last_error = error
                        if isinstance(error, FofaError) and error.retryable and attempt < self.settings.max_retries:
                            delay = _backoff_delay(attempt, retry_after, self.settings.retry_backoff_sec)
                            remaining = deadline - _clock()
                            if delay >= remaining:
                                break
                            await _sleep(min(delay, remaining))
                            continue
                        if isinstance(error, FofaError):
                            if error.error_type in {"rate_limit", "quota_exceeded", "authentication_error"}:
                                break
                        raise error
                    self.accounts.mark_success(state)
                    data["_account_used"] = state.config.label
                    if len(tried) > 1:
                        data["_accounts_tried"] = tried
                    return data
                except httpx.RequestError:
                    if attempt < self.settings.max_retries:
                        delay = _backoff_delay(attempt, None, self.settings.retry_backoff_sec)
                        remaining = deadline - _clock()
                        if delay >= remaining:
                            break
                        await _sleep(min(delay, remaining))
                        continue
                    error = NetworkError("FOFA network request failed", account=state.config.label)
                    last_error = error
                    self.accounts.mark_failure(state, error)
                    logger.warning(
                        "FOFA network error account=%s; trying next account",
                        state.config.label,
                    )
                    break
                except FofaError:
                    raise
        if last_error is not None:
            raise last_error
        raise RemoteApiError("all configured FOFA accounts failed")

    @staticmethod
    def _encoded(query: str) -> str:
        return base64.b64encode(query.encode("utf-8")).decode("ascii")

    async def search(self, query: str, fields: list[str], size: int, page: int) -> dict[str, Any]:
        return await self._request(
            "search/all",
            lambda account: {
                "email": account.config.email,
                "key": account.config.key,
                "qbase64": self._encoded(query),
                "fields": ",".join(fields),
                "size": size,
                "page": page,
            },
        )

    async def search_next(
        self, query: str, fields: list[str], size: int, next_token: str, full: bool
    ) -> dict[str, Any]:
        params = {
            "qbase64": self._encoded(query),
            "fields": ",".join(fields),
            "size": size,
            "full": str(full).lower(),
        }
        if next_token:
            params["next"] = next_token
        return await self._request(
            "search/next",
            lambda account: {**params, "key": account.config.key},
        )

    async def stats(self, query: str, fields: list[str], size: int) -> dict[str, Any]:
        return await self._request(
            "search/stats",
            lambda account: {
                "key": account.config.key,
                "qbase64": self._encoded(query),
                "fields": ",".join(fields),
                "size": size,
            },
        )

    async def host(self, host: str, detail: bool) -> dict[str, Any]:
        return await self._request(
            f"host/{quote(host, safe='')}",
            lambda account: {"key": account.config.key, "detail": str(detail).lower()},
        )

    async def account_info(self) -> dict[str, Any]:
        return await self._request("info/my", lambda account: {"key": account.config.key})
