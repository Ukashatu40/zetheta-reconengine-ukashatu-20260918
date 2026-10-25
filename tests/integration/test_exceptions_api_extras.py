"""Exception API additions: filters, assignment, SLA scan; and a full run that
auto-resolves and queues what it creates."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.api.deps import get_clock, get_db, get_now
from recon.api.main import app
from recon.audit.chains import EXCEPTIONS_CHAIN, MATCHING_CHAIN, RUNS_CHAIN
from recon.audit.verifier import verify_chain
from recon.persistence.models import ApiKey, AuditLog, ExceptionEvent, ReconException
from recon.security.api_keys import generate_api_key, hash_api_key
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)
_MON = date(2026, 3, 16)


def _clock() -> Callable[[], datetime]:
    return lambda: _NOW


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_now] = lambda: _NOW
    app.dependency_overrides[get_clock] = _clock
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _headers(session: Session, role: str) -> dict[str, str]:
    raw = generate_api_key()
    session.add(ApiKey(key_hash=hash_api_key(raw), name=f"k-{uuid.uuid4().hex[:8]}", role=role))
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
        "created_at": _NOW,
        "sla_deadline": _NOW + timedelta(hours=4),
    }
    values.update(overrides)
    row = ReconException(**values)
    session.add(row)
    session.flush()
    return row


def test_exceptions_filter_by_tier_assignee_and_created_date(
    client: TestClient, db_session: Session
) -> None:
    viewer = _headers(db_session, "VIEWER")
    _exc(db_session, assigned_tier=2, assigned_to="queue:analyst")
    _exc(db_session, assigned_tier=3, assigned_to="queue:senior-ops")
    _exc(db_session, assigned_tier=2, assigned_to="priya", created_at=_NOW - timedelta(days=3))

    def total(query: str) -> int:
        return int(client.get(f"/api/v1/exceptions?{query}", headers=viewer).json()["total"])

    assert total("tier=2") == 2
    assert total("tier=3") == 1
    assert total("assigned_to=priya") == 1
    assert total("date_from=2026-03-16") == 2
    assert total("date_to=2026-03-14") == 1
    assert total("date_from=2026-03-13&date_to=2026-03-16&tier=2") == 2
    assert client.get("/api/v1/exceptions?tier=9", headers=viewer).status_code == 422


def test_an_analyst_can_assign_an_open_exception_and_it_is_audited(
    client: TestClient, db_session: Session
) -> None:
    exc = _exc(db_session)
    response = client.put(
        f"/api/v1/exceptions/{exc.id}/assign",
        headers=_headers(db_session, "ANALYST"),
        json={"assignee": "priya", "note": "knows this bank"},
    )
    assert response.status_code == 200
    assert response.json()["assigned_to"] == "priya"
    event = db_session.scalars(
        select(ExceptionEvent).where(ExceptionEvent.exception_id == exc.id)
    ).one()
    assert (event.event_type, event.detail) == ("ASSIGNED", "assigned to priya")
    entry = db_session.scalars(select(AuditLog).where(AuditLog.chain_id == EXCEPTIONS_CHAIN)).one()
    assert (entry.action_type, entry.before_state, entry.after_state) == (
        "ASSIGN",
        {"assigned_to": None},
        {"assigned_to": "priya"},
    )
    assert entry.rationale == "knows this bank"


def test_assignment_needs_analyst_and_an_open_exception(
    client: TestClient, db_session: Session
) -> None:
    exc = _exc(db_session)
    body = {"assignee": "priya"}
    assert (
        client.put(
            f"/api/v1/exceptions/{exc.id}/assign", headers=_headers(db_session, "VIEWER"), json=body
        ).status_code
        == 403
    )
    analyst = _headers(db_session, "ANALYST")
    assert (
        client.put(
            f"/api/v1/exceptions/{uuid.uuid4()}/assign", headers=analyst, json=body
        ).status_code
        == 404
    )
    exc.status = "RESOLVED"
    db_session.flush()
    assert (
        client.put(f"/api/v1/exceptions/{exc.id}/assign", headers=analyst, json=body).status_code
        == 409
    )
    assert (
        client.put(
            f"/api/v1/exceptions/{exc.id}/assign", headers=analyst, json={"assignee": ""}
        ).status_code
        == 422
    )


def test_the_sla_scan_endpoint_escalates_breached_exceptions_for_an_admin(
    client: TestClient, db_session: Session
) -> None:
    exc = _exc(db_session, sla_deadline=_NOW - timedelta(minutes=1))
    assert (
        client.post(
            "/api/v1/exceptions/sla-scan", headers=_headers(db_session, "ANALYST")
        ).status_code
        == 403
    )
    body = client.post("/api/v1/exceptions/sla-scan", headers=_headers(db_session, "ADMIN")).json()
    assert body == {"escalated_count": 1, "breached_at_top_tier_count": 0}
    db_session.refresh(exc)
    assert (exc.assigned_tier, exc.status) == (3, "ESCALATED")


def test_a_full_run_resolves_tier_1_queues_the_rest_and_audits_everything(
    client: TestClient, db_session: Session
) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all(
        [
            # a fee-shaped gap: Tier 1, resolved automatically
            make_txn(
                f.id,
                source="INTERNAL",
                txn_date=_MON,
                normalised_reference="FEEREF",
                amount_minor=100_000,
                txn_id="FEEREF",
            ),
            make_txn(
                f.id,
                source="EXTERNAL",
                txn_date=_MON,
                normalised_reference="FEEREF",
                amount_minor=99_400,
                txn_id="FEEREF",
            ),
            # a bank entry the ledger has never seen: Tier 2, queued for an analyst
            make_txn(
                f.id,
                source="EXTERNAL",
                txn_date=_MON,
                normalised_reference="NOLEDGER",
                txn_id="NOLEDGER",
            ),
            # a record under an AML hold: Tier 4, queued for compliance
            make_txn(
                f.id,
                source="EXTERNAL",
                txn_date=_MON,
                normalised_reference="HELD1",
                txn_id="HELD1",
                narration_normalised="AML HOLD",
            ),
        ]
    )
    db_session.flush()
    analyst = _headers(db_session, "ANALYST")

    run = client.post(
        "/api/v1/reconciliation/run",
        headers={**analyst, "Idempotency-Key": uuid.uuid4().hex},
        json={"bank_code": "HDFC"},
    ).json()

    assert run["status"] == "COMPLETED", run["error_summary"]
    metrics = run["metrics"]
    assert metrics["exceptions_auto_resolved"] >= 1
    by_category = {
        e.category: e for e in db_session.query(ReconException).filter_by(run_id=run["id"])
    }
    assert by_category["MISSING_INTERNAL"].assigned_to == "queue:analyst"
    assert by_category["REGULATORY_HOLD"].assigned_to == "queue:compliance"
    assert {
        e.status for e in by_category.values() if e.category in ("FEE_DEDUCTION", "AMOUNT_MISMATCH")
    } <= {"RESOLVED", "OPEN"}

    # the run is on the audit chain from start to finish, and the match set is fingerprinted
    runs = db_session.scalars(
        select(AuditLog).where(AuditLog.chain_id == RUNS_CHAIN).order_by(AuditLog.sequence_no)
    ).all()
    assert [
        (r.before_state["status"], r.after_state["status"])
        for r in runs
        if r.before_state and r.after_state
    ] == [
        ("NONE", "RUNNING"),
        ("RUNNING", "COMPLETED"),
    ]
    summary = db_session.scalars(
        select(AuditLog).where(
            AuditLog.chain_id == MATCHING_CHAIN, AuditLog.actor_id == "run-service"
        )
    ).one()
    assert summary.after_state is not None
    assert len(summary.after_state["match_result_ids_sha256"]) == 64
    for chain in (RUNS_CHAIN, MATCHING_CHAIN, EXCEPTIONS_CHAIN):
        assert verify_chain(db_session, chain).ok is True
