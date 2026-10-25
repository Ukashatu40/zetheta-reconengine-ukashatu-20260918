"""Cache semantics and the rate limiter, with no server involved."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from recon import cache
from recon.api.deps import get_db
from recon.api.main import app
from recon.cache import MemoryCache, RedisCache


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_memory_cache_returns_a_value_until_its_ttl_passes() -> None:
    clock = _Clock()
    store = MemoryCache(clock)
    store.set("k", "v", 30)
    assert store.get("k") == "v"
    clock.now += 29.9
    assert store.get("k") == "v"
    clock.now += 0.2
    assert store.get("k") is None


def test_memory_cache_counter_window_starts_at_the_first_increment_and_resets() -> None:
    clock = _Clock()
    store = MemoryCache(clock)
    assert [store.incr_window("c", 60) for _ in range(3)] == [1, 2, 3]
    clock.now += 61
    assert store.incr_window("c", 60) == 1


def test_an_unreachable_redis_fails_open_instead_of_raising() -> None:
    dead = RedisCache("redis://127.0.0.1:1/0")
    dead.set("k", "v", 5)  # swallowed
    assert dead.get("k") is None
    assert dead.incr_window("c", 60) == 0  # 0 means "unknown": the limiter allows the request
    assert dead.ping() is False


def test_get_cache_uses_memory_when_redis_is_not_configured() -> None:
    assert isinstance(cache.get_cache(), MemoryCache)


def test_get_cache_uses_redis_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    assert isinstance(cache.get_cache(), RedisCache)


class _NoKeys:
    """A session in which no API key exists: every presented key is unknown."""

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(first=lambda: None)


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = _NoKeys
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_requests_over_the_limit_get_429_with_retry_after(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    codes = [client.get("/api/v1/audit", headers={"X-API-Key": "k1"}).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]  # 401: the key is unknown, but it was counted
    blocked = client.get("/api/v1/audit", headers={"X-API-Key": "k1"})
    assert blocked.headers["Retry-After"].isdigit()
    assert blocked.json()["status"] == 429
    assert blocked.headers["content-type"].startswith("application/problem+json")


def test_the_limit_is_per_identity(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    assert client.get("/api/v1/audit", headers={"X-API-Key": "a"}).status_code == 401
    assert client.get("/api/v1/audit", headers={"X-API-Key": "b"}).status_code == 401
    assert client.get("/api/v1/audit", headers={"X-API-Key": "a"}).status_code == 429


def test_uploads_have_their_own_lower_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "100")
    monkeypatch.setenv("RATE_LIMIT_UPLOAD_PER_MINUTE", "1")
    first = client.post("/api/v1/files/upload", headers={"X-API-Key": "u"})
    second = client.post("/api/v1/files/upload", headers={"X-API-Key": "u"})
    assert (first.status_code, second.status_code) == (401, 429)
    assert client.get("/api/v1/audit", headers={"X-API-Key": "u"}).status_code == 401


def test_health_endpoints_are_never_limited(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    assert all(client.get("/health/live").status_code == 200 for _ in range(5))


def test_a_limit_of_zero_disables_limiting(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
    assert all(
        client.get("/api/v1/audit", headers={"X-API-Key": "z"}).status_code == 401 for _ in range(5)
    )


def test_rate_limit_headers_report_the_remaining_budget(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "10")
    response = client.get("/api/v1/audit", headers={"X-API-Key": "h"})
    assert (response.headers["X-RateLimit-Limit"], response.headers["X-RateLimit-Remaining"]) == (
        "10",
        "9",
    )
