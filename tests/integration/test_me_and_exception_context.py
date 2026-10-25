"""GET /me and GET /exceptions/{id}/context, the two reads the web dashboard depends on."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from recon.api.deps import get_db
from recon.api.main import app
from recon.persistence.models import ApiKey, MatchResult, ReconException
from recon.security.api_keys import generate_api_key, hash_api_key
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _headers(session: Session, role: str) -> dict[str, str]:
    raw = generate_api_key()
    session.add(ApiKey(key_hash=hash_api_key(raw), name=f"k-{uuid.uuid4().hex[:8]}", role=role))
    session.flush()
    return {"X-API-Key": raw}


@pytest.mark.parametrize("role", ["VIEWER", "ANALYST", "ADMIN"])
def test_me_reports_the_name_and_role_of_the_key(
    client: TestClient, db_session: Session, role: str
) -> None:
    headers = _headers(db_session, role)
    body = client.get("/api/v1/me", headers=headers).json()
    assert body["role"] == role and body["name"].startswith("k-")


def test_me_needs_a_key(client: TestClient) -> None:
    assert client.get("/api/v1/me").status_code == 401


def _exception_with_pair(session: Session) -> tuple[ReconException, MatchResult]:
    f = make_ingestion_file(session)
    internal = make_txn(f.id, source="INTERNAL", counterparty_account_masked="XXXX1234")
    external = make_txn(f.id, source="EXTERNAL", id=uuid.uuid4(), raw_transaction_id=uuid.uuid4())
    session.add_all([internal, external])
    session.flush()
    match = MatchResult(
        run_id="r",
        match_type="FUZZY",
        status="PENDING_REVIEW",
        confidence=0.75,
        internal_transaction_id=internal.id,
        external_transaction_id=external.id,
        field_scores={"amount": 0.0},
        matched_fields={},
        rationale="review band",
        matched_on_date=_NOW.date(),
    )
    session.add(match)
    session.flush()
    row = ReconException(
        dedupe_key=uuid.uuid4().hex,
        category="AMOUNT_MISMATCH",
        severity="MEDIUM",
        status="OPEN",
        assigned_tier=2,
        bank_code="HDFC",
        affected_records={
            "match_result_id": str(match.id),
            "internal_transaction_id": str(internal.id),
            "external_transaction_id": str(external.id),
        },
        suggested_resolution="x",
        rationale="y",
        created_at=_NOW,
        sla_deadline=_NOW + timedelta(hours=2),
    )
    session.add(row)
    session.flush()
    return row, match


def test_context_returns_both_transactions_and_the_match(
    client: TestClient, db_session: Session
) -> None:
    row, match = _exception_with_pair(db_session)
    body = client.get(
        f"/api/v1/exceptions/{row.id}/context", headers=_headers(db_session, "VIEWER")
    ).json()
    assert {t["source"] for t in body["transactions"]} == {"INTERNAL", "EXTERNAL"}
    assert [m["id"] for m in body["matches"]] == [str(match.id)]
    assert "counterparty_account" not in body["transactions"][0]
    assert any(t["counterparty_account_masked"] == "XXXX1234" for t in body["transactions"])


def test_context_for_an_exception_with_no_records_is_empty(
    client: TestClient, db_session: Session
) -> None:
    row, _ = _exception_with_pair(db_session)
    row.affected_records = {"line_numbers": [4]}
    db_session.flush()
    body = client.get(
        f"/api/v1/exceptions/{row.id}/context", headers=_headers(db_session, "VIEWER")
    ).json()
    assert body == {"transactions": [], "matches": []}


def test_context_of_an_unknown_exception_is_404(client: TestClient, db_session: Session) -> None:
    response = client.get(
        f"/api/v1/exceptions/{uuid.uuid4()}/context", headers=_headers(db_session, "VIEWER")
    )
    assert response.status_code == 404
