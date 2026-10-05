from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .errors import ConfigurationError


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def env_file_path() -> Path:
    configured = os.getenv("FOFA_ENV_FILE", "").strip()
    if not configured:
        return project_root() / ".env"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else project_root() / path


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    value = os.getenv(name, str(default)).strip()
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    value = os.getenv(name, str(default)).strip()
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return parsed


@dataclass(frozen=True)
class AccountConfig:
    label: str
    email: str
    key: str


@dataclass(frozen=True)
class Settings:
    api_base: str
    timeout_sec: int
    default_page_size: int
    max_page_size: int
    max_cursor_pages: int
    max_requests_per_session: int
    max_estimated_fpoints: int
    max_total_results: int
    max_query_length: int
    max_retries: int
    max_host_response_bytes: int
    log_level: str
    accounts: tuple[AccountConfig, ...]
    # 重试退避与单次调用总时限（秒）。带默认值放尾部，保持既有关键字构造兼容。
    retry_backoff_sec: float = 0.5
    max_call_seconds: float = 90.0

    @classmethod
    def load(cls) -> "Settings":
        env_file = env_file_path()
        if env_file.exists() and not env_file.is_file():
            raise ConfigurationError(f"FOFA_ENV_FILE is not a file: {env_file}")
        load_dotenv(env_file, override=False)

        accounts = tuple(
            account
            for account in (
                AccountConfig("primary", os.getenv("FOFA_EMAIL", "").strip(), os.getenv("FOFA_KEY", "").strip()),
                AccountConfig(
                    "backup",
                    os.getenv("FOFA_EMAIL_BACKUP", "").strip(),
                    os.getenv("FOFA_KEY_BACKUP", "").strip(),
                ),
                AccountConfig(
                    "backup2",
                    os.getenv("FOFA_EMAIL_BACKUP2", "").strip(),
                    os.getenv("FOFA_KEY_BACKUP2", "").strip(),
                ),
            )
            if account.key
        )
        if not accounts:
            raise ConfigurationError(
                f"No FOFA key configured. Add FOFA_KEY to {env_file}; "
                "then retry with: uv run fofa-mcp"
            )

        base = os.getenv("FOFA_API_BASE", "https://fofa.info/api/v1").strip().rstrip("/")
        if not base.startswith(("https://", "http://")):
            raise ConfigurationError("FOFA_API_BASE must be an HTTP(S) URL")

        return cls(
            api_base=base,
            timeout_sec=_int_env("FOFA_TIMEOUT_SEC", 30, 1, 300),
            default_page_size=_int_env("FOFA_DEFAULT_PAGE_SIZE", 100, 1, 1000),
            max_page_size=_int_env("FOFA_MAX_PAGE_SIZE", 200, 1, 10000),
            max_cursor_pages=_int_env("FOFA_MAX_CURSOR_PAGES", 20, 1, 1000),
            max_requests_per_session=_int_env(
                "FOFA_MAX_REQUESTS_PER_SESSION", 100, 1, 10000
            ),
            max_estimated_fpoints=_int_env(
                "FOFA_MAX_ESTIMATED_FPOINTS", 30, 1, 100000
            ),
            max_total_results=_int_env(
                "FOFA_MAX_TOTAL_RESULTS", 2000, 1, 1000000
            ),
            max_query_length=_int_env("FOFA_MAX_QUERY_LENGTH", 4096, 64, 100000),
            max_retries=_int_env("FOFA_MAX_RETRIES", 2, 0, 10),
            max_host_response_bytes=_int_env(
                "FOFA_MAX_HOST_RESPONSE_BYTES", 2_000_000, 1024, 50_000_000
            ),
            log_level=os.getenv("FOFA_LOG_LEVEL", "INFO").strip().upper(),
            accounts=accounts,
            retry_backoff_sec=_float_env("FOFA_RETRY_BACKOFF_SEC", 0.5, 0.0, 30.0),
            max_call_seconds=_float_env("FOFA_MAX_CALL_SECONDS", 90.0, 5.0, 600.0),
        )
