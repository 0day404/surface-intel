from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable


class RateLimiter:
    """Per-key minimum-interval pacing with injectable clock and sleeper.

    Callers pass both `adapter:<name>` and `target:<value>` keys so per-adapter
    and per-target limits hold on independent timelines. The sleeper is
    awaited (never busy-waited); tests inject a fake clock and a recording
    sleeper so pacing is verified without real delays.
    """

    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ):
        if min_interval < 0:
            raise ValueError("invalid_min_interval")
        self.min_interval = min_interval
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or asyncio.sleep
        self._last: dict[str, float] = {}

    async def acquire(self, *keys: str) -> float:
        """ Pace to at least `min_interval` since the last acquire per key.

        Returns the longest wait this acquire performed.
        """
        waited = 0.0
        for key in keys:
            last = self._last.get(key)
            now = self._clock()
            if last is not None:
                remaining = self.min_interval - (now - last)
                if remaining > 0:
                    await self._sleeper(remaining)
                    waited = max(waited, remaining)
                    now = self._clock()
            self._last[key] = now
        return waited
