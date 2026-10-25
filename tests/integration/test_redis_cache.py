"""RedisCache against a real Redis (the one in docker compose). Skipped when no
server answers at TEST_REDIS_URL (default redis://localhost:6379/15)."""

from __future__ import annotations

import os
import time
import uuid

import pytest

from recon.cache import RedisCache

_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture()
def store() -> RedisCache:
    cache = RedisCache(_URL)
    if not cache.ping():
        pytest.skip(f"no Redis at {_URL}")
    return cache


def test_a_value_round_trips_and_expires(store: RedisCache) -> None:
    key = f"test:{uuid.uuid4().hex}"
    store.set(key, "hello", 1)
    assert store.get(key) == "hello"
    time.sleep(1.2)
    assert store.get(key) is None


def test_the_counter_window_is_fixed_from_the_first_increment(store: RedisCache) -> None:
    key = f"test:rl:{uuid.uuid4().hex}"
    assert [store.incr_window(key, 60) for _ in range(3)] == [1, 2, 3]
    ttl = store._client.ttl(key)
    assert 0 < ttl <= 60  # set once: later increments must not extend the window
    store._client.delete(key)
