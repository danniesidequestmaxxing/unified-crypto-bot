"""Async-safe Token Bucket rate limiter.

Each API service gets its own bucket so they are throttled independently.
The bucket refills at a constant rate and blocks callers via asyncio.sleep
when empty — no busy-waiting.
"""
from __future__ import annotations

import asyncio
import time


class TokenBucket:
    """Token bucket rate limiter safe for concurrent asyncio coroutines.

    Args:
        rate: Tokens added per second (e.g. 120 rpm → 2.0 tok/s).
        capacity: Maximum burst size.  Defaults to ``rate`` (1 second burst).
    """

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> None:
        """Block until *tokens* are available, then consume them."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self.rate
            await asyncio.sleep(wait)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now
