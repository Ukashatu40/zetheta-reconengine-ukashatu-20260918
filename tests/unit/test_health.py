"""Health endpoints. Readiness is checked against a stub session so the unit
suite needs no database."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from recon.api.deps import get_db
from recon.api.main import app

client = TestClient(app)


class _Session:
    def __init__(self, fail: bool) -> None:
        self._fail = fail

    def execute(self, *_args: Any, **_kwargs: Any) -> None:
        if self._fail:
            raise RuntimeError("database is down")


@pytest.fixture()
def database(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, bool]]:
    monkeypatch.delenv("REDIS_URL", raising=False)
    state = {"fail": False}
    app.dependency_overrides[get_db] = lambda: _Session(state["fail"])
    try:
        yield state
    finally:
        app.dependency_overrides.clear()


def test_health_live_returns_ok() -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_reports_each_dependency(database: dict[str, bool]) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "not_configured"},
    }


def test_health_ready_is_503_when_the_database_is_down(database: dict[str, bool]) -> None:
    database["fail"] = True
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "down"
