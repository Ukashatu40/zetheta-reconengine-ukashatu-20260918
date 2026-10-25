# src/recon/cache.py
"""Key/value cache and counters used for dashboard caching (R78) and rate
limiting (R76).

Redis is used when REDIS_URL is set. Every Redis failure is swallowed and
logged: a cache outage must slow the dashboard down, never take it (or the
rate limiter) offline, so reads miss and counters report 0 (fail open). When
REDIS_URL is unset the process-local MemoryCache is used, which is correct for
a single worker and for tests but is not shared between workers.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Protocol

import redis

logger = logging.getLogger("recon.cache")
_MAX_COUNTERS = 50_000  # bound memory under key churn


class Cache(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl_seconds: int) -> None: ...

    def incr_window(self, key: str, ttl_seconds: int) -> int:
        """Increments a counter that expires ttl_seconds after its first
        increment; returns the new value (0 if the backend is unavailable)."""
        ...

    def ping(self) -> bool: ...


class MemoryCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._values: dict[str, tuple[float, str]] = {}
        self._counters: dict[str, tuple[float, int]] = {}

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._values.get(key)
            if entry is None:
                return None
            if entry[0] <= self._clock():
                del self._values[key]
                return None
            return entry[1]

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        with self._lock:
            self._values[key] = (self._clock() + ttl_seconds, value)

    def incr_window(self, key: str, ttl_seconds: int) -> int:
        now = self._clock()
        with self._lock:
            expires, count = self._counters.get(key, (0.0, 0))
            if expires <= now:
                expires, count = now + ttl_seconds, 0
            count += 1
            self._counters[key] = (expires, count)
            if len(self._counters) > _MAX_COUNTERS:
                self._counters = {k: v for k, v in self._counters.items() if v[0] > now}
            return count

    def ping(self) -> bool:
        return True


class RedisCache:
    def __init__(self, url: str) -> None:
        self._client = redis.Redis.from_url(
            url, socket_connect_timeout=0.5, socket_timeout=0.5, decode_responses=True
        )

    def get(self, key: str) -> str | None:
        try:
            value = self._client.get(key)
        except Exception:
            logger.warning("redis get failed; treating as a cache miss", exc_info=True)
            return None
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        try:
            self._client.set(key, value, ex=ttl_seconds)
        except Exception:
            logger.warning("redis set failed; value not cached", exc_info=True)

    def incr_window(self, key: str, ttl_seconds: int) -> int:
        try:
            pipe = self._client.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl_seconds, nx=True)
            count = pipe.execute()[0]
        except Exception:
            logger.warning("redis incr failed; failing open", exc_info=True)
            return 0
        return int(count)

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False


@lru_cache(maxsize=1)
def get_cache() -> Cache:
    url = os.environ.get("REDIS_URL")
    if url:
        return RedisCache(url)
    return MemoryCache()


def redis_configured() -> bool:
    return bool(os.environ.get("REDIS_URL"))
