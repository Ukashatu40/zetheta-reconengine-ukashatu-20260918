"""Suite-wide isolation: the cache is a process-level singleton, so every test
starts with a fresh one and never talks to a real Redis by accident."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from recon import cache


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("REDIS_URL", raising=False)
    cache.get_cache.cache_clear()
    yield
    cache.get_cache.cache_clear()
