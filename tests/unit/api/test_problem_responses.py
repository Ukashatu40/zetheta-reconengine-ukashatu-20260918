# tests/unit/api/test_problem_responses.py
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from recon.api.deps import get_db
from recon.api.main import app


def _unused_db() -> Session:
    """Stands in for get_db. Never queried: authentication fails first."""
    return Session()


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = _unused_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_unknown_route_is_problem_json_with_a_matching_request_id(client: TestClient) -> None:
    response = client.get("/nope")
    body = response.json()
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["status"] == 404
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_a_valid_incoming_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"


def test_an_invalid_incoming_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "has spaces and <junk>"})
    assert response.headers["X-Request-ID"] != "has spaces and <junk>"
    assert len(response.headers["X-Request-ID"]) == 36  # a generated UUID


def test_a_missing_api_key_is_401_with_a_challenge(client: TestClient) -> None:
    response = client.get("/api/v1/exceptions")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "ApiKey"
    assert response.json()["detail"] == "missing or invalid API key"


def test_health_endpoints_need_no_key(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200
