# tests/integration/test_matches_api.py
from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.api.deps import get_db, get_now
from recon.api.main import app
from recon.audit.chains import MATCHING_CHAIN
from recon.audit.verifier import verify_chain
from recon.persistence.models import ApiKey, AuditLog, MatchClaim
from recon.persistence.repositories.matching import MatchingRepository
from recon.security.api_keys import generate_api_key, hash_api_key
from recon.security.rbac import Role
from tests.integration.factories import make_pending_review_pair

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_now] = lambda: _NOW
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _headers(session: Session, role: Role, name: str | None = None) -> dict[str, str]:
    raw = generate_api_key()
    session.add(
        ApiKey(
            key_hash=hash_api_key(raw), name=name or f"k-{uuid.uuid4().hex[:8]}", role=role.value
        )
    )
    session.flush()
    return {"X-API-Key": raw}


def _confirm(client: TestClient, headers: dict[str, str], match_id: uuid.UUID, **body: Any) -> Any:
    return client.put(f"/api/v1/matches/{match_id}/confirm", headers=headers, json=body)


def _reject(client: TestClient, headers: dict[str, str], match_id: uuid.UUID, **body: Any) -> Any:
    payload = {"reason": "different payments"} | body
    return client.put(f"/api/v1/matches/{match_id}/reject", headers=headers, json=payload)


def test_the_pending_queue_lists_only_pending_matches_by_default(
    client: TestClient, db_session: Session
) -> None:
    _, _, pending = make_pending_review_pair(db_session)
    _, _, decided = make_pending_review_pair(db_session)
    decided.status = "CONFIRMED"
    db_session.flush()
    viewer = _headers(db_session, Role.VIEWER)

    queue = client.get("/api/v1/matches", headers=viewer).json()
    assert [m["id"] for m in queue["items"]] == [str(pending.id)]
    assert queue["items"][0]["confidence"] == "0.877"

    confirmed = client.get("/api/v1/matches", headers=viewer, params={"status": "CONFIRMED"}).json()
    assert confirmed["total"] == 1
    assert (
        client.get("/api/v1/matches", headers=viewer, params={"status": "BOGUS"}).status_code == 422
    )


def test_a_viewer_cannot_review(client: TestClient, db_session: Session) -> None:
    _, _, result = make_pending_review_pair(db_session)
    viewer = _headers(db_session, Role.VIEWER)
    assert _confirm(client, viewer, result.id).status_code == 403
    assert _reject(client, viewer, result.id).status_code == 403
    db_session.refresh(result)
    assert result.status == "PENDING_REVIEW"


def test_an_analyst_confirms_and_the_effect_is_visible_everywhere(
    client: TestClient, db_session: Session
) -> None:
    internal, external, result = make_pending_review_pair(db_session)

    response = _confirm(
        client, _headers(db_session, Role.ANALYST, "alice"), result.id, note="checked"
    )

    body = response.json()
    assert (response.status_code, body["status"], body["reviewed_by"]) == (
        200,
        "CONFIRMED",
        "alice",
    )
    assert (internal.match_status, external.match_status) == ("MATCHED", "MATCHED")
    entry = db_session.scalars(select(AuditLog).where(AuditLog.chain_id == MATCHING_CHAIN)).one()
    assert (entry.action_type, entry.actor_id, entry.rationale) == (
        "MATCH_CONFIRM",
        "api-key:alice",
        "checked",
    )
    assert verify_chain(db_session, MATCHING_CHAIN).ok is True
    summary = client.get(
        "/api/v1/dashboard/summary", headers=_headers(db_session, Role.VIEWER)
    ).json()
    assert summary["pending_review_count"] == 0


def test_confirming_without_a_body_is_allowed(client: TestClient, db_session: Session) -> None:
    _, _, result = make_pending_review_pair(db_session)
    response = client.put(
        f"/api/v1/matches/{result.id}/confirm", headers=_headers(db_session, Role.ANALYST)
    )
    assert response.status_code == 200


def test_reject_releases_the_claims_and_frees_the_transactions(
    client: TestClient, db_session: Session
) -> None:
    internal, external, result = make_pending_review_pair(db_session)

    response = _reject(
        client, _headers(db_session, Role.ANALYST), result.id, reason="wrong customer"
    )

    assert (response.status_code, response.json()["status"]) == (200, "REJECTED")
    claims = db_session.scalars(
        select(MatchClaim).where(MatchClaim.match_result_id == result.id)
    ).all()
    assert {c.status for c in claims} == {"RELEASED"}
    assert (internal.match_status, external.match_status) == ("UNMATCHED", "UNMATCHED")
    assert [t.id for t in MatchingRepository(db_session).find_unmatched("HDFC", "INTERNAL")] == [
        internal.id
    ]


def test_a_reason_is_required_to_reject(client: TestClient, db_session: Session) -> None:
    _, _, result = make_pending_review_pair(db_session)
    analyst = _headers(db_session, Role.ANALYST)
    assert (
        client.put(f"/api/v1/matches/{result.id}/reject", headers=analyst, json={}).status_code
        == 422
    )
    assert _reject(client, analyst, result.id, reason="   ").status_code == 422
    db_session.refresh(result)
    assert result.status == "PENDING_REVIEW"


def test_deciding_twice_is_409_in_every_combination(
    client: TestClient, db_session: Session
) -> None:
    _, _, first = make_pending_review_pair(db_session)
    _, _, second = make_pending_review_pair(db_session)
    analyst = _headers(db_session, Role.ANALYST)
    assert _confirm(client, analyst, first.id).status_code == 200
    assert _confirm(client, analyst, first.id).status_code == 409
    assert _reject(client, analyst, first.id).status_code == 409
    assert _reject(client, analyst, second.id).status_code == 200
    assert _confirm(client, analyst, second.id).status_code == 409


def test_an_unknown_match_is_404_and_a_malformed_id_is_422(
    client: TestClient, db_session: Session
) -> None:
    analyst = _headers(db_session, Role.ANALYST)
    assert _confirm(client, analyst, uuid.uuid4()).status_code == 404
    assert (
        client.put("/api/v1/matches/not-a-uuid/confirm", headers=analyst, json={}).status_code
        == 422
    )


def test_an_inconsistent_pair_is_409_and_untouched(client: TestClient, db_session: Session) -> None:
    internal, _, result = make_pending_review_pair(db_session)
    internal.match_status = "MATCHED"
    db_session.flush()

    response = _confirm(client, _headers(db_session, Role.ANALYST), result.id)

    assert response.status_code == 409
    db_session.refresh(result)
    assert result.status == "PENDING_REVIEW"
