"""Rate limiting for the endpoints an attacker gets to call unauthenticated.

The limiter is behind a small protocol with an in-process sliding-window
implementation. That is honest for a single-instance deployment and honest
about its limit: N replicas mean N times the allowance. Swapping in Redis is
one class implementing :class:`RateLimiterBackend` and one line in
``lifespan`` -- deliberately not a dependency this starter forces on you.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import RateLimitedError

_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


@dataclass(frozen=True, slots=True)
class Rate:
    times: int
    seconds: int

    @classmethod
    def parse(cls, spec: str) -> Rate:
        """``"10/minute"`` -> ``Rate(10, 60)``."""
        count, _, unit = spec.partition("/")
        unit = unit.strip().lower().rstrip("s")
        if unit not in _UNITS:
            raise ValueError(f"Unknown rate unit: {spec!r}")
        return cls(times=int(count), seconds=_UNITS[unit])


class RateLimiterBackend(Protocol):
    async def hit(self, key: str, rate: Rate) -> int:
        """Record one hit; return seconds to wait, or 0 when allowed."""


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def hit(self, key: str, rate: Rate) -> int:
        now = time.monotonic()
        async with self._lock:
            window = self._hits[key]
            cutoff = now - rate.seconds
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= rate.times:
                return max(1, int(window[0] + rate.seconds - now) + 1)
            window.append(now)
            if not window:
                del self._hits[key]
            return 0

    async def reset(self) -> None:
        async with self._lock:
            self._hits.clear()


_backend: RateLimiterBackend = InMemoryRateLimiter()


def set_backend(backend: RateLimiterBackend) -> None:
    global _backend
    _backend = backend


def get_backend() -> RateLimiterBackend:
    return _backend


def client_key(request: Request) -> str:
    """Identify the caller.

    ``X-Forwarded-For`` is honoured only when the deployment says it is behind
    a proxy; a spoofable header can otherwise be used to *escape* a bucket.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimit:
    """Dependency: ``Depends(RateLimit("10/minute", scope="login"))``."""

    def __init__(self, spec: str, *, scope: str) -> None:
        self.rate = Rate.parse(spec)
        self.scope = scope

    async def __call__(self, request: Request) -> None:
        if not get_settings().rate_limit_enabled:
            return
        key = f"{self.scope}:{client_key(request)}"
        retry_after = await get_backend().hit(key, self.rate)
        if retry_after:
            raise RateLimitedError(
                "Too many requests. Please retry later.",
                extra={"retry_after_seconds": retry_after},
            )
