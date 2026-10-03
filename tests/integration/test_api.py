# tests/integration/test_api.py
"""API tests against the real database. The get_db dependency is replaced
by the test's rolled-back session, so every request sees the test's data."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from recon.api.deps import get_db, get_now
from recon.api.main import app
from recon.audit.chains import EXCEPTIONS_CHAIN
from recon.audit.verifier import verify_chain
from recon.persistence.models import ApiKey, AuditLog, ExceptionEvent, ReconException
from recon.security.api_keys import generate_api_key, hash_api_key
from recon.security.rbac import Role
from tests.integration.factories import make_ingestion_file, make_txn

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


def _exc(session: Session, **overrides: Any) -> ReconException:
    values: dict[str, Any] = {
        "dedupe_key": uuid.uuid4().hex,
        "category": "MISSING_INTERNAL",
        "severity": "HIGH",
        "status": "OPEN",
        "assigned_tier": 2,
        "bank_code": "HDFC",
        "affected_records": {},
        "suggested_resolution": "x",
        "rationale": "y",
        "sla_deadline": _NOW + timedelta(hours=4),
    }
    values.update(overrides)
    row = ReconException(**values)
    session.add(row)
    session.flush()
    return row


def _resolve(
    client: TestClient, headers: dict[str, str], exception_id: uuid.UUID, **body: Any
) -> Any:
    payload = {"resolution_details": "checked with the bank"} | body
    return client.put(f"/api/v1/exceptions/{exception_id}/resolve", headers=headers, json=payload)


# -- authentication ------------------------------------------------------


def test_unknown_key_is_rejected_without_echoing_it(client: TestClient) -> None:
    response = client.get("/api/v1/exceptions", headers={"X-API-Key": "rk_not-a-real-key"})
    assert response.status_code == 401
    assert "rk_not-a-real-key" not in response.text


def test_revoked_key_is_rejected(client: TestClient, db_session: Session) -> None:
    raw = generate_api_key()
    db_session.add(ApiKey(key_hash=hash_api_key(raw), name="old", role="ADMIN", revoked_at=_NOW))
    db_session.flush()
    assert client.get("/api/v1/exceptions", headers={"X-API-Key": raw}).status_code == 401


@pytest.mark.parametrize("role", list(Role))
def test_every_role_can_read(client: TestClient, db_session: Session, role: Role) -> None:
    response = client.get("/api/v1/exceptions", headers=_headers(db_session, role))
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]


# -- listing and detail ----------------------------------------------------


def test_list_orders_by_deadline_filters_and_paginates(
    client: TestClient, db_session: Session
) -> None:
    h = _headers(db_session, Role.VIEWER)
    _exc(db_session, category="FORMAT_ERROR", sla_deadline=_NOW + timedelta(hours=2))
    _exc(db_session, category="MISSING_INTERNAL", sla_deadline=_NOW + timedelta(hours=1))
    _exc(
        db_session,
        category="FORMAT_ERROR",
        status="RESOLVED",
        sla_deadline=_NOW + timedelta(hours=3),
    )

    body = client.get("/api/v1/exceptions", headers=h).json()
    assert body["total"] == 3
    assert [i["category"] for i in body["items"]] == [
        "MISSING_INTERNAL",
        "FORMAT_ERROR",
        "FORMAT_ERROR",
    ]

    filtered = client.get(
        "/api/v1/exceptions", headers=h, params={"category": "FORMAT_ERROR", "status": "OPEN"}
    )
    assert filtered.json()["total"] == 1

    page = client.get("/api/v1/exceptions", headers=h, params={"limit": 2, "offset": 2}).json()
    assert (len(page["items"]), page["total"]) == (1, 3)


@pytest.mark.parametrize(
    "params", [{"severity": "BOGUS"}, {"limit": 0}, {"limit": 201}, {"offset": -1}]
)
def test_invalid_query_parameters_are_422_problem_json(
    client: TestClient, db_session: Session, params: dict[str, Any]
) -> None:
    response = client.get(
        "/api/v1/exceptions", headers=_headers(db_session, Role.VIEWER), params=params
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_detail_includes_events_and_unknown_ids_are_404(
    client: TestClient, db_session: Session
) -> None:
    h = _headers(db_session, Role.VIEWER)
    exc = _exc(db_session)
    db_session.add(
        ExceptionEvent(
            exception_id=exc.id, event_type="CREATED", actor="t", detail="d", occurred_at=_NOW
        )
    )
    db_session.flush()

    detail = client.get(f"/api/v1/exceptions/{exc.id}", headers=h)
    assert detail.status_code == 200
    assert [e["event_type"] for e in detail.json()["events"]] == ["CREATED"]
    assert client.get(f"/api/v1/exceptions/{uuid.uuid4()}", headers=h).status_code == 404
    assert client.get("/api/v1/exceptions/not-a-uuid", headers=h).status_code == 422


def test_money_is_serialised_as_a_string(client: TestClient, db_session: Session) -> None:
    _exc(db_session, financial_impact_amount=Decimal("1500.0000"), financial_impact_currency="INR")
    item = client.get("/api/v1/exceptions", headers=_headers(db_session, Role.VIEWER)).json()[
        "items"
    ][0]
    assert item["financial_impact_amount"] == "1500.0000"


# -- resolve: authority --------------------------------------------------------


def test_viewer_cannot_resolve_and_nothing_changes(client: TestClient, db_session: Session) -> None:
    exc = _exc(db_session)
    response = _resolve(client, _headers(db_session, Role.VIEWER), exc.id)
    assert response.status_code == 403
    db_session.refresh(exc)
    assert exc.status == "OPEN"


def test_analyst_resolves_a_tier_2_exception_and_it_is_recorded_everywhere(
    client: TestClient, db_session: Session
) -> None:
    exc = _exc(db_session)
    response = _resolve(client, _headers(db_session, Role.ANALYST, name="alice"), exc.id)

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["resolved_by"]) == ("RESOLVED", "alice")
    db_session.refresh(exc)
    assert exc.resolved_at == _NOW
    events = db_session.scalars(
        select(ExceptionEvent).where(ExceptionEvent.exception_id == exc.id)
    ).all()
    assert [e.event_type for e in events] == ["RESOLVED"]
    entries = db_session.scalars(
        select(AuditLog).where(AuditLog.chain_id == EXCEPTIONS_CHAIN)
    ).all()
    assert [(e.action_type, e.actor_id) for e in entries] == [("RESOLVE", "api-key:alice")]
    assert entries[0].before_state == {"status": "OPEN", "tier": 2}
    assert verify_chain(db_session, EXCEPTIONS_CHAIN).ok is True


def test_analyst_cannot_resolve_a_tier_4_exception_but_admin_can(
    client: TestClient, db_session: Session
) -> None:
    exc = _exc(db_session, category="DIRECTION_REVERSAL", severity="CRITICAL", assigned_tier=4)

    denied = _resolve(client, _headers(db_session, Role.ANALYST), exc.id)
    assert denied.status_code == 403
    db_session.refresh(exc)
    assert exc.status == "OPEN"

    allowed = _resolve(client, _headers(db_session, Role.ADMIN), exc.id)
    assert allowed.status_code == 200


def test_write_off_needs_admin_even_at_tier_1(client: TestClient, db_session: Session) -> None:
    exc = _exc(db_session, assigned_tier=1)
    assert (
        _resolve(
            client, _headers(db_session, Role.ANALYST), exc.id, outcome="WRITTEN_OFF"
        ).status_code
        == 403
    )
    admin = _resolve(client, _headers(db_session, Role.ADMIN), exc.id, outcome="WRITTEN_OFF")
    assert (admin.status_code, admin.json()["status"]) == (200, "WRITTEN_OFF")


def test_resolving_twice_is_409(client: TestClient, db_session: Session) -> None:
    exc = _exc(db_session)
    h = _headers(db_session, Role.ANALYST)
    assert _resolve(client, h, exc.id).status_code == 200
    assert _resolve(client, h, exc.id).status_code == 409


@pytest.mark.parametrize(
    "body", [{"resolution_details": "   "}, {"resolution_details": "x", "outcome": "DELETED"}]
)
def test_invalid_resolve_bodies_are_422(
    client: TestClient, db_session: Session, body: dict[str, Any]
) -> None:
    exc = _exc(db_session)
    response = client.put(
        f"/api/v1/exceptions/{exc.id}/resolve",
        headers=_headers(db_session, Role.ANALYST),
        json=body,
    )
    assert response.status_code == 422


def test_resolving_an_unknown_exception_is_404(client: TestClient, db_session: Session) -> None:
    assert _resolve(client, _headers(db_session, Role.ANALYST), uuid.uuid4()).status_code == 404


# -- audit -----------------------------------------------------------------------


def test_audit_listing_filters_by_action(client: TestClient, db_session: Session) -> None:
    exc = _exc(db_session)
    _resolve(client, _headers(db_session, Role.ANALYST), exc.id)

    body = client.get(
        "/api/v1/audit",
        headers=_headers(db_session, Role.VIEWER),
        params={"action_type": "RESOLVE"},
    ).json()

    assert body["total"] == 1
    assert body["items"][0]["action_type"] == "RESOLVE"
    assert len(body["items"][0]["current_hash"]) == 64


def test_verify_endpoint_passes_then_detects_tampering(
    client: TestClient, db_session: Session
) -> None:
    exc = _exc(db_session)
    _resolve(client, _headers(db_session, Role.ANALYST), exc.id)
    viewer = _headers(db_session, Role.VIEWER)

    clean = client.get("/api/v1/audit/verify", headers=viewer).json()
    assert clean["ok"] is True
    assert clean["chains"][EXCEPTIONS_CHAIN]["entries_checked"] == 1

    db_session.execute(
        text("ALTER TABLE audit.audit_log DISABLE TRIGGER audit_log_forbid_mutation")
    )
    db_session.execute(
        text("UPDATE audit.audit_log SET rationale = 'forged' WHERE chain_id = :c"),
        {"c": EXCEPTIONS_CHAIN},
    )
    db_session.execute(text("ALTER TABLE audit.audit_log ENABLE TRIGGER audit_log_forbid_mutation"))

    tampered = client.get("/api/v1/audit/verify", headers=viewer).json()
    assert tampered["ok"] is False
    assert tampered["chains"][EXCEPTIONS_CHAIN]["failure_kind"] == "HASH_MISMATCH"


# -- dashboard ----------------------------------------------------------------------


def test_dashboard_summary_counts(client: TestClient, db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all(
        [
            make_txn(f.id, source="INTERNAL", match_status="MATCHED"),
            make_txn(f.id, source="EXTERNAL", match_status="MATCHED"),
            make_txn(f.id, source="EXTERNAL", match_status="UNMATCHED"),
        ]
    )
    _exc(db_session, category="MISSING_INTERNAL")
    _exc(db_session, category="FORMAT_ERROR", status="ESCALATED", sla_breached_at=_NOW)
    _exc(db_session, category="FORMAT_ERROR", status="RESOLVED")
    db_session.flush()

    body = client.get("/api/v1/dashboard/summary", headers=_headers(db_session, Role.VIEWER)).json()

    assert (body["total_transactions"], body["matched_count"]) == (3, 2)
    assert body["match_rate_of_total"] == round(2 / 3, 4)
    assert body["exception_count"] == 3
    assert body["unresolved_count"] == 2
    assert body["sla_breached_open_count"] == 1
    assert body["exceptions_by_category"] == {"MISSING_INTERNAL": 1, "FORMAT_ERROR": 2}
    assert body["exceptions_by_severity"] == {"HIGH": 3}
    assert body["pending_review_count"] == 0
