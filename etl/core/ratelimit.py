"""In-process token bucket with an injectable clock.

Used by `etl.core.http` and later by the LLM router.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable


class TokenBucket:
    """Thread-simple token bucket; not safe for multiprocessing."""

    def __init__(
        self,
        rate: float,
        capacity: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.rate = float(rate)
        self.capacity = float(capacity)
        self._clock = clock or time.monotonic
        self._tokens = float(capacity)
        self._last = self._clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last = now

    @property
    def available(self) -> float:
        self._refill()
        return self._tokens

    def take(self, n: float = 1.0) -> bool:
        """Consume `n` tokens if available; return True on success."""
        if n <= 0:
            raise ValueError("n must be positive")
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def time_until(self, n: float = 1.0) -> float:
        """Seconds until `n` tokens are available (0 if already available)."""
        if n <= 0:
            raise ValueError("n must be positive")
        self._refill()
        if self._tokens >= n:
            return 0.0
        return (n - self._tokens) / self.rate

    async def acquire(
        self,
        n: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """Wait (via `sleep`) until `n` tokens can be consumed."""
        do_sleep = sleep or asyncio.sleep
        while True:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return
            delay = (n - self._tokens) / self.rate
            # Sleep in one go; tests inject a fake sleep that advances the clock.
            await do_sleep(delay)

    def __repr__(self) -> str:
        return f"TokenBucket(rate={self.rate}, capacity={self.capacity})"


__all__ = ["TokenBucket"]
