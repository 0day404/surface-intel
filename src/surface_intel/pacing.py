"""Adapter pacing: env-configurable rate limiters wired into every runner.

`SURFACE_INTEL_MIN_INTERVAL_<KEY>` (seconds, float) overrides the default
minimum interval between actions for an adapter. Defaults pace the CT log
sources (crt.sh / ctlogs.dev anonymous quota) and RDAP; everything else
defaults to unpaced. Invalid or negative values fail closed with a
ValueError naming the variable, rather than silently running unpaced.
"""
from __future__ import annotations

import os
from typing import Awaitable, Callable

from .ratelimit import RateLimiter

# 秒；ct 2s 对应 crt.sh/ctlogs 匿名额度（ctlogs.dev 匿名 100 req/h），
# rdap 1s 对应注册局查询礼仪。其余适配器默认不限速。
DEFAULT_MIN_INTERVALS: dict[str, float] = {
    "ct": 2.0,
    "rdap": 1.0,
}

ENV_PREFIX = "SURFACE_INTEL_MIN_INTERVAL_"


def configured_rate_limiter(
    adapter_key: str,
    *,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
) -> RateLimiter:
    env_name = ENV_PREFIX + adapter_key.upper()
    raw = os.getenv(env_name, "").strip()
    if raw:
        try:
            interval = float(raw)
        except ValueError as exc:
            raise ValueError(f"invalid_{env_name}") from exc
        if interval < 0:
            raise ValueError(f"invalid_{env_name}")
    else:
        interval = DEFAULT_MIN_INTERVALS.get(adapter_key, 0.0)
    return RateLimiter(interval, clock=clock, sleeper=sleeper)
