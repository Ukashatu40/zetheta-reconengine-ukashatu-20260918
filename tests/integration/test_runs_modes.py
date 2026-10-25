"""Run parameters: the spec-named endpoints, date range, FAST mode, replay rules."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from recon.api.deps import get_clock, get_db
from recon.api.main import app
from recon.persistence.models import ApiKey, MatchResult
from recon.security.api_keys import generate_api_key, hash_api_key
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _clock() -> Callable[[], datetime]:
    return lambda: _NOW


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_clock] = _clock
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def analyst(db_session: Session) -> dict[str, str]:
    raw = generate_api_key()
    db_session.add(
        ApiKey(key_hash=hash_api_key(raw), name=f"a-{uuid.uuid4().hex[:6]}", role="ANALYST")
    )
    db_session.flush()
    return {"X-API-Key": raw}


def _run(
    client: TestClient, headers: dict[str, str], body: dict[str, Any], key: str | None = None
) -> Any:
    return client.post(
        "/api/v1/reconciliation/run",
        headers={**headers, "Idempotency-Key": key or uuid.uuid4().hex},
        json=body,
    )


def _pair(session: Session, day: date, ref: str) -> None:
    f = make_ingestion_file(session)
    session.add_all(
        [
            make_txn(f.id, source="INTERNAL", txn_date=day, normalised_reference=ref, txn_id=ref),
            make_txn(f.id, source="EXTERNAL", txn_date=day, normalised_reference=ref, txn_id=ref),
        ]
    )
    session.flush()


def test_the_spec_named_endpoints_work_end_to_end(
    client: TestClient, db_session: Session, analyst: dict[str, str]
) -> None:
    _pair(db_session, date(2026, 3, 15), "REF100")

    created = _run(client, analyst, {"bank_code": "HDFC"})
    run_id = created.json()["id"]

    assert created.status_code == 201
    assert created.json()["mode"] == "FULL"
    runs = client.get("/api/v1/reconciliation/runs", headers=analyst).json()
    assert [r["id"] for r in runs["items"]] == [run_id]
    results = client.get(f"/api/v1/reconciliation/runs/{run_id}/results", headers=analyst).json()
    assert results["total"] == 1


def test_the_original_paths_remain_as_deprecated_aliases(
    client: TestClient, db_session: Session, analyst: dict[str, str]
) -> None:
    schema = client.get("/openapi.json").json()["paths"]
    assert schema["/api/v1/reconcile"]["post"]["deprecated"] is True
    assert "deprecated" not in schema["/api/v1/reconciliation/run"]["post"]
    assert schema["/api/v1/upload"]["post"]["deprecated"] is True
    assert schema["/api/v1/files/upload"]["post"]


def test_a_date_range_limits_the_pools(
    client: TestClient, db_session: Session, analyst: dict[str, str]
) -> None:
    _pair(db_session, date(2026, 3, 10), "REF010")
    _pair(db_session, date(2026, 3, 15), "REF015")

    body = _run(
        client, analyst, {"bank_code": "HDFC", "date_from": "2026-03-14", "date_to": "2026-03-15"}
    ).json()

    assert body["metrics"]["exact_matched"] == 1
    assert (body["date_from"], body["date_to"]) == ("2026-03-14", "2026-03-15")
    matched = db_session.query(MatchResult).one()
    assert matched.matched_on_date == date(2026, 3, 15)


def test_fast_mode_runs_exact_matching_only(
    client: TestClient, db_session: Session, analyst: dict[str, str]
) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all(
        [  # an exact pair, and a pair only fuzzy matching would find (reference differs by a digit)
            make_txn(f.id, source="INTERNAL", normalised_reference="REF100", txn_id="REF100"),
            make_txn(f.id, source="EXTERNAL", normalised_reference="REF100", txn_id="REF100"),
            make_txn(
                f.id,
                source="INTERNAL",
                normalised_reference="ABC2000001",
                amount_minor=7777,
                amount=77.77,
            ),
            make_txn(
                f.id,
                source="EXTERNAL",
                normalised_reference="ABC2000002",
                amount_minor=7777,
                amount=77.77,
            ),
        ]
    )
    db_session.flush()

    fast = _run(client, analyst, {"bank_code": "HDFC", "mode": "FAST"}).json()
    assert (fast["metrics"]["exact_matched"], fast["metrics"]["fuzzy_auto_matched"]) == (1, 0)
    assert fast["metrics"]["pending_review"] == 0

    full = _run(client, analyst, {"bank_code": "HDFC", "mode": "FULL"}).json()
    assert full["metrics"]["exact_matched"] == 0  # already matched by the fast pass
    assert full["metrics"]["fuzzy_auto_matched"] + full["metrics"]["pending_review"] == 1


def test_an_inverted_date_range_is_rejected(client: TestClient, analyst: dict[str, str]) -> None:
    response = _run(
        client, analyst, {"bank_code": "HDFC", "date_from": "2026-03-16", "date_to": "2026-03-15"}
    )
    assert response.status_code == 422


def test_an_unknown_mode_is_rejected(client: TestClient, analyst: dict[str, str]) -> None:
    assert _run(client, analyst, {"bank_code": "HDFC", "mode": "TURBO"}).status_code == 422


def test_replaying_a_key_with_a_different_range_or_mode_is_a_conflict(
    client: TestClient, analyst: dict[str, str]
) -> None:
    assert _run(client, analyst, {"bank_code": "HDFC"}, key="key-modes-0001").status_code == 201
    assert _run(client, analyst, {"bank_code": "HDFC"}, key="key-modes-0001").status_code == 200
    changed_mode = _run(
        client, analyst, {"bank_code": "HDFC", "mode": "FAST"}, key="key-modes-0001"
    )
    changed_range = _run(
        client, analyst, {"bank_code": "HDFC", "date_from": "2026-03-01"}, key="key-modes-0001"
    )
    assert (changed_mode.status_code, changed_range.status_code) == (409, 409)
