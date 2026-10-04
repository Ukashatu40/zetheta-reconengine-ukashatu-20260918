# tests/integration/test_runs_api.py
from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from recon.api.deps import get_clock, get_db
from recon.api.main import app
from recon.persistence.models import ApiKey, MatchResult, ReconciliationRun
from recon.security.api_keys import generate_api_key, hash_api_key
from recon.security.rbac import Role
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _fixed_clock() -> Callable[[], datetime]:
    return lambda: _NOW


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_clock] = _fixed_clock
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _headers(session: Session, role: Role, name: str = "tester") -> dict[str, str]:
    raw = generate_api_key()
    session.add(
        ApiKey(key_hash=hash_api_key(raw), name=f"{name}-{uuid.uuid4().hex[:6]}", role=role.value)
    )
    session.flush()
    return {"X-API-Key": raw}


def _seed_pair(session: Session) -> None:
    f = make_ingestion_file(session)
    session.add_all([make_txn(f.id, source="INTERNAL"), make_txn(f.id, source="EXTERNAL")])
    session.flush()


def _post(
    client: TestClient, headers: dict[str, str], key: str | None = None, bank: str = "HDFC"
) -> Any:
    return client.post(
        "/api/v1/reconcile",
        headers={**headers, "Idempotency-Key": key or uuid.uuid4().hex},
        json={"bank_code": bank},
    )


def test_a_viewer_cannot_start_a_run(client: TestClient, db_session: Session) -> None:
    assert _post(client, _headers(db_session, Role.VIEWER)).status_code == 403
    assert db_session.query(ReconciliationRun).count() == 0


def test_an_analyst_starts_a_run_that_matches_and_reports(
    client: TestClient, db_session: Session
) -> None:
    _seed_pair(db_session)
    response = _post(client, _headers(db_session, Role.ANALYST))

    body = response.json()
    assert response.status_code == 201
    assert (body["status"], body["bank_code"]) == ("COMPLETED", "HDFC")
    assert body["metrics"]["exact_matched"] == 1
    assert body["error_summary"] is None


def test_the_idempotency_key_is_required_and_validated(
    client: TestClient, db_session: Session
) -> None:
    h = _headers(db_session, Role.ANALYST)
    assert (
        client.post("/api/v1/reconcile", headers=h, json={"bank_code": "HDFC"}).status_code == 422
    )
    short = client.post(
        "/api/v1/reconcile", headers={**h, "Idempotency-Key": "abc"}, json={"bank_code": "HDFC"}
    )
    assert short.status_code == 422


@pytest.mark.parametrize("bank", ["hdfc", "H", "HDFC BANK", "A" * 21, ""])
def test_invalid_bank_codes_are_rejected(
    client: TestClient, db_session: Session, bank: str
) -> None:
    assert _post(client, _headers(db_session, Role.ANALYST), bank=bank).status_code == 422


def test_replaying_a_key_returns_200_and_does_not_rerun(
    client: TestClient, db_session: Session
) -> None:
    _seed_pair(db_session)
    h = _headers(db_session, Role.ANALYST)

    first = _post(client, h, key="key-api-replay-1")
    second = _post(client, h, key="key-api-replay-1")

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    assert db_session.query(ReconciliationRun).count() == 1
    assert db_session.query(MatchResult).count() == 1


def test_the_same_key_for_a_different_bank_is_409(client: TestClient, db_session: Session) -> None:
    h = _headers(db_session, Role.ANALYST)
    _post(client, h, key="key-api-conflict", bank="HDFC")
    assert _post(client, h, key="key-api-conflict", bank="ICICI").status_code == 409


def test_a_bank_with_a_run_in_progress_is_409(client: TestClient, db_session: Session) -> None:
    db_session.add(
        ReconciliationRun(
            idempotency_key="other-key-0001", bank_code="HDFC", status="RUNNING", requested_by="x",
            created_at=_NOW, started_at=_NOW,
        )
    )  # fmt: skip
    db_session.flush()
    assert _post(client, _headers(db_session, Role.ANALYST)).status_code == 409


def test_a_failing_run_is_recorded_without_leaking_the_error(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Boom:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def run(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("password=hunter2")

    monkeypatch.setattr("recon.runs.service.MatchingOrchestrator", Boom)

    response = _post(client, _headers(db_session, Role.ANALYST))

    assert response.status_code == 201
    assert (response.json()["status"], response.json()["error_summary"]) == (
        "FAILED",
        "RuntimeError",
    )
    assert "hunter2" not in response.text


def test_runs_list_filters_and_paginates(client: TestClient, db_session: Session) -> None:
    a = _headers(db_session, Role.ANALYST)
    _post(client, a, bank="HDFC")
    _post(client, a, bank="ICICI")
    v = _headers(db_session, Role.VIEWER)

    assert client.get("/api/v1/runs", headers=v).json()["total"] == 2
    only = client.get("/api/v1/runs", headers=v, params={"bank_code": "ICICI"}).json()
    assert [r["bank_code"] for r in only["items"]] == ["ICICI"]
    assert len(client.get("/api/v1/runs", headers=v, params={"limit": 1}).json()["items"]) == 1
    assert client.get("/api/v1/runs", headers=v, params={"status": "BOGUS"}).status_code == 422


def test_results_for_a_run_are_listed_with_string_confidence(
    client: TestClient, db_session: Session
) -> None:
    _seed_pair(db_session)
    run_id = _post(client, _headers(db_session, Role.ANALYST)).json()["id"]
    v = _headers(db_session, Role.VIEWER)

    body = client.get(f"/api/v1/runs/{run_id}/results", headers=v).json()

    assert body["total"] == 1
    assert (body["items"][0]["match_type"], body["items"][0]["confidence"]) == ("EXACT", "1.000")
    other = client.get(
        f"/api/v1/runs/{run_id}/results", headers=v, params={"status": "PENDING_REVIEW"}
    ).json()
    assert other["total"] == 0


def test_results_for_an_unknown_run_are_404(client: TestClient, db_session: Session) -> None:
    v = _headers(db_session, Role.VIEWER)
    assert client.get(f"/api/v1/runs/{uuid.uuid4()}/results", headers=v).status_code == 404
