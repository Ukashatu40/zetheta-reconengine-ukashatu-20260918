# tests/unit/api/test_route_protection.py
"""Every route under /api must authenticate. Checked two ways that do not
depend on FastAPI's router internals: the OpenAPI document must declare the
API-key security scheme on every operation, and an anonymous request to
every operation must be answered 401."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from recon.api.deps import get_db
from recon.api.main import app

_PUBLIC_PATHS = {"/health/live", "/health/ready"}
_METHODS = {"get", "post", "put", "patch", "delete"}
_OPERATIONS = [
    (method, path)
    for path, item in app.openapi()["paths"].items()
    if path not in _PUBLIC_PATHS
    for method in item
    if method in _METHODS
]


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


def test_the_check_is_not_vacuous() -> None:
    # list, detail, resolve, audit list, audit verify, dashboard summary
    assert len(_OPERATIONS) >= 6


@pytest.mark.parametrize(("method", "path"), _OPERATIONS)
def test_every_operation_declares_the_api_key_scheme(method: str, path: str) -> None:
    assert app.openapi()["paths"][path][method].get(
        "security"
    ), f"{method} {path} is not declared as secured"


@pytest.mark.parametrize(("method", "path"), _OPERATIONS)
def test_every_operation_rejects_an_anonymous_request(
    client: TestClient, method: str, path: str
) -> None:
    url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
    response = client.request(
        method.upper(), url, json={} if method in {"post", "put", "patch"} else None
    )
    assert (
        response.status_code == 401
    ), f"{method} {url} answered {response.status_code} without a key"


def test_every_operation_is_versioned() -> None:
    assert all(path.startswith("/api/v1/") for _, path in _OPERATIONS)
