"""Backlog recovery (B4.3), near-real-time reconcile-on-upload (C1), manual matches
and unmatching, and exception override / reopen."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.api.deps import get_clock, get_db, get_now
from recon.api.main import app
from recon.api.v1.runs import get_backlog_session_factory, get_parallel_map
from recon.api.v1.upload import get_upload_dir
from recon.audit.chains import EXCEPTIONS_CHAIN, MATCHING_CHAIN
from recon.audit.verifier import verify_chain
from recon.persistence.models import (
    ApiKey,
    AuditLog,
    ExceptionEvent,
    MatchClaim,
    MatchResult,
    NormalisedTransaction,
    ReconciliationRun,
    ReconException,
)
from recon.runs.backlog import BacklogService, BankBacklogResult
from recon.runs.service import RunService
from recon.runs.settings import default_settings
from recon.security.api_keys import generate_api_key, hash_api_key
from tests.integration.factories import (
    make_ingestion_file,
    make_pending_review_pair,
    make_txn,
)

_NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)
_DAY = date(2026, 3, 16)
_HDFC_HEADER = "Transaction Reference,Value Date,Amount,Dr/Cr,Counterparty Name,Narration\n"


def _serial(
    work: Callable[[str], BankBacklogResult], banks: list[str]
) -> Iterable[BankBacklogResult]:
    return [work(b) for b in banks]


@pytest.fixture()
def client(db_session: Session, tmp_path: Path) -> Iterator[TestClient]:
    @contextmanager
    def shared() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_now] = lambda: _NOW
    app.dependency_overrides[get_clock] = lambda: (lambda: _NOW)
    app.dependency_overrides[get_upload_dir] = lambda: tmp_path / "uploads"
    app.dependency_overrides[get_backlog_session_factory] = lambda: shared
    app.dependency_overrides[get_parallel_map] = lambda: _serial
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _key(session: Session, role: str = "ANALYST") -> dict[str, str]:
    raw = generate_api_key()
    session.add(ApiKey(key_hash=hash_api_key(raw), name=f"k-{uuid.uuid4().hex[:8]}", role=role))
    session.flush()
    return {"X-API-Key": raw}


def _pair(
    session: Session, bank: str, day: date, ref: str, **external: Any
) -> tuple[NormalisedTransaction, NormalisedTransaction]:
    f = make_ingestion_file(session, bank_code=bank)
    base: dict[str, Any] = {
        "bank_code": bank,
        "txn_date": day,
        "normalised_reference": ref,
        "txn_id": ref,
    }
    a = make_txn(f.id, source="INTERNAL", **base)
    b = make_txn(f.id, source="EXTERNAL", **(base | external))
    session.add_all([a, b])
    session.flush()
    return a, b


# -- backlog recovery ----------------------------------------------------------------------


def _backlog(
    client: TestClient, headers: dict[str, str], key: str = "backlog-key-0001", **body: Any
) -> Any:
    return client.post(
        "/api/v1/reconciliation/backlog", headers={**headers, "Idempotency-Key": key}, json=body
    )


def test_the_backlog_is_worked_newest_day_first_for_every_bank(
    client: TestClient, db_session: Session
) -> None:
    for bank in ("HDFC", "ICICI"):
        for offset in (0, 1, 2):
            _pair(db_session, bank, _DAY - timedelta(days=offset), f"{bank}{offset}")

    response = _backlog(client, _key(db_session), banks=["ICICI", "HDFC"])

    body = response.json()
    assert response.status_code == 201
    assert [b["bank_code"] for b in body["banks"]] == ["HDFC", "ICICI"]
    for bank in body["banks"]:
        assert [d["day"] for d in bank["days"]] == ["2026-03-16", "2026-03-15", "2026-03-14"]
        assert (bank["days_processed"], bank["exact_matched"], bank["failed_days"]) == (3, 3, [])
    runs = db_session.query(ReconciliationRun).filter_by(mode="FAST").all()
    assert len(runs) == 6 and {r.status for r in runs} == {"COMPLETED"}
    assert db_session.query(NormalisedTransaction).filter_by(match_status="UNMATCHED").count() == 0


def test_the_first_pass_is_exact_only_and_the_full_pipeline_follows_in_the_background(
    client: TestClient, db_session: Session
) -> None:
    _pair(db_session, "HDFC", _DAY, "EXACT1")
    # only fuzzy matching can pair this one: the references differ by a digit
    _pair(db_session, "HDFC", _DAY, "ABC2000001", normalised_reference="ABC2000002")

    body = _backlog(client, _key(db_session), banks=["HDFC"]).json()

    assert body["queued_full_runs"] == 1
    fast = db_session.query(ReconciliationRun).filter_by(mode="FAST").one()
    assert fast.metrics is not None and fast.metrics["exact_matched"] == 1
    full = db_session.get(ReconciliationRun, uuid.UUID(body["banks"][0]["queued_full_run_id"]))
    assert full is not None and full.mode == "FULL"
    assert full.status == "COMPLETED"  # the background task ran after the response
    assert full.metrics is not None
    assert full.metrics["pending_review"] + full.metrics["fuzzy_auto_matched"] == 1


def test_replaying_the_request_reuses_the_runs(client: TestClient, db_session: Session) -> None:
    _pair(db_session, "HDFC", _DAY, "R1")
    headers = _key(db_session)
    _backlog(client, headers, banks=["HDFC"])
    count = db_session.query(ReconciliationRun).count()

    replay = _backlog(client, headers, banks=["HDFC"])

    assert replay.status_code == 201
    assert db_session.query(ReconciliationRun).count() == count
    assert replay.json()["queued_full_runs"] == 0  # the queued run already ran


def test_a_date_range_and_the_queue_switch_are_honoured(
    client: TestClient, db_session: Session
) -> None:
    _pair(db_session, "HDFC", _DAY, "NEW")
    _pair(db_session, "HDFC", date(2026, 3, 1), "OLD")

    body = _backlog(
        client, _key(db_session), banks=["HDFC"], date_from="2026-03-10", queue_full=False
    ).json()

    assert [d["day"] for d in body["banks"][0]["days"]] == ["2026-03-16"]
    assert body["queued_full_runs"] == 0
    assert db_session.query(ReconciliationRun).filter_by(mode="FULL").count() == 0


def test_the_response_carries_the_preliminary_recovery_figures(
    client: TestClient, db_session: Session
) -> None:
    for i in range(3):
        _pair(db_session, "HDFC", _DAY, f"P{i}")
    body = _backlog(client, _key(db_session), banks=["HDFC"], queue_full=False).json()
    assert body["recovery"]["backlog_unmatched_transactions"] == 0
    assert "projected_hours_to_clear" in body["recovery"]


def test_unknown_banks_bad_ranges_and_missing_rights_are_rejected(
    client: TestClient, db_session: Session
) -> None:
    headers = _key(db_session)
    assert _backlog(client, headers, banks=["NOPE"]).status_code == 422
    assert (
        _backlog(client, headers, date_from="2026-03-16", date_to="2026-03-01").status_code == 422
    )
    assert _backlog(client, _key(db_session, "VIEWER")).status_code == 403
    missing_key = client.post("/api/v1/reconciliation/backlog", headers=headers, json={})
    assert missing_key.status_code == 422  # the idempotency key is required


def test_a_queued_run_that_finds_its_bank_busy_fails_cleanly_instead_of_overlapping(
    db_session: Session,
) -> None:
    service = RunService(db_session, default_settings(), lambda: _NOW)
    queued = service.start(
        bank_code="HDFC", idempotency_key="queued-0001", requested_by="t", defer=True
    )
    assert queued.run.status == "PENDING" and queued.run.started_at is None
    service.start(bank_code="HDFC", idempotency_key="running-0001", requested_by="t")

    @contextmanager
    def shared() -> Iterator[Session]:
        yield db_session

    BacklogService(shared, default_settings(), lambda: _NOW, _serial).run_queued(queued.run.id)

    db_session.refresh(queued.run)
    assert queued.run.status == "FAILED"
    assert "another run" in (queued.run.error_summary or "")


# -- reconcile on upload (C1) --------------------------------------------------------------


def _upload_csv(
    client: TestClient, headers: dict[str, str], name: str, rows: str, **form: str
) -> Any:
    data = {"bank_code": "HDFC", "source": "EXTERNAL", "format_type": "CSV", **form}
    return client.post(
        "/api/v1/files/upload",
        headers=headers,
        data=data,
        files={"file": (name, _HDFC_HEADER + rows, "text/csv")},
    )


def test_an_upload_can_trigger_exact_matching_straight_away(
    client: TestClient, db_session: Session
) -> None:
    headers = _key(db_session)
    ledger = "UTR1,15-03-2026,100.00,CR,Acme,x\n"
    _upload_csv(client, headers, "ledger.csv", ledger, source="INTERNAL")

    response = _upload_csv(
        client,
        headers,
        "bank.csv",
        ledger + "UTR2,15-03-2026,5.00,CR,Other,y\n",
        auto_reconcile="true",
    )

    body = response.json()
    assert response.status_code == 201 and body["reconciliation_run_id"] is not None
    run = db_session.get(ReconciliationRun, uuid.UUID(body["reconciliation_run_id"]))
    assert run is not None and (run.mode, run.status) == ("FAST", "COMPLETED")
    assert run.metrics is not None and run.metrics["exact_matched"] == 1


def test_an_upload_without_the_flag_does_not_reconcile(
    client: TestClient, db_session: Session
) -> None:
    body = _upload_csv(
        client, _key(db_session), "bank.csv", "UTR1,15-03-2026,100.00,CR,Acme,x\n"
    ).json()
    assert body["reconciliation_run_id"] is None
    assert db_session.query(ReconciliationRun).count() == 0


# -- manual matches and unmatching ---------------------------------------------------------


def _manual(
    client: TestClient,
    headers: dict[str, str],
    internal: NormalisedTransaction,
    external: NormalisedTransaction,
    note: str = "same payment",
) -> Any:
    return client.post(
        "/api/v1/matches/manual",
        headers=headers,
        json={
            "internal_transaction_id": str(internal.id),
            "external_transaction_id": str(external.id),
            "note": note,
        },
    )


def test_an_analyst_can_pair_two_transactions_by_hand(
    client: TestClient, db_session: Session
) -> None:
    a, b = _pair(db_session, "HDFC", _DAY, "LEFT", normalised_reference="RIGHT")

    response = _manual(client, _key(db_session), a, b, "reference was keyed wrongly")

    assert response.status_code == 201
    body = response.json()
    assert (body["match_type"], body["status"], body["confidence"]) == (
        "MANUAL",
        "CONFIRMED",
        "1.000",
    )
    assert body["rationale"] == "reference was keyed wrongly" and body["reviewed_by"]
    assert (a.match_status, b.match_status) == ("MATCHED", "MATCHED")
    assert db_session.query(MatchClaim).filter_by(status="ACTIVE").count() == 2
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.chain_id == MATCHING_CHAIN, AuditLog.action_type == "MATCH")
    ).one()
    assert entry.actor_type == "USER" and entry.rationale == "reference was keyed wrongly"


def test_a_manual_match_refuses_what_it_should(client: TestClient, db_session: Session) -> None:
    headers = _key(db_session)
    a, b = _pair(db_session, "HDFC", _DAY, "X1")
    c, _ = _pair(db_session, "ICICI", _DAY, "X2")
    d, e = _pair(db_session, "HDFC", _DAY, "X3")
    ghost = make_txn(uuid.uuid4(), source="EXTERNAL")

    assert _manual(client, headers, b, a).status_code == 422  # sides the wrong way round
    assert _manual(client, headers, a, e).status_code == 201
    assert _manual(client, headers, a, b).status_code == 409  # already matched
    assert _manual(client, headers, c, b).status_code == 422  # different banks
    assert _manual(client, headers, d, ghost).status_code == 404
    assert _manual(client, _key(db_session, "VIEWER"), d, e).status_code == 403
    empty_note = {
        "internal_transaction_id": str(d.id),
        "external_transaction_id": str(e.id),
        "note": "",
    }
    assert (
        client.post("/api/v1/matches/manual", headers=headers, json=empty_note).status_code == 422
    )


def test_a_pair_held_for_review_cannot_be_overridden_by_a_manual_match(
    client: TestClient, db_session: Session
) -> None:
    a, b, _ = make_pending_review_pair(db_session)
    assert _manual(client, _key(db_session), a, b).status_code == 409


def _exact_pair_matched(
    client: TestClient, db_session: Session, headers: dict[str, str]
) -> tuple[NormalisedTransaction, NormalisedTransaction, MatchResult]:
    a, b = _pair(db_session, "HDFC", _DAY, "EXACT")
    client.post(
        "/api/v1/reconciliation/run",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json={"bank_code": "HDFC"},
    )
    return a, b, db_session.query(MatchResult).one()


def test_unmatching_frees_the_transactions_and_no_later_run_matches_the_pair_again(
    client: TestClient, db_session: Session
) -> None:
    headers = _key(db_session)
    a, b, match = _exact_pair_matched(client, db_session, headers)
    assert (a.match_status, b.match_status) == ("MATCHED", "MATCHED")

    response = client.post(
        f"/api/v1/matches/{match.id}/unmatch", headers=headers, json={"reason": "wrong customer"}
    )

    assert response.status_code == 200 and response.json()["status"] == "REJECTED"
    db_session.refresh(a)
    db_session.refresh(b)
    assert (a.match_status, b.match_status) == ("UNMATCHED", "UNMATCHED")
    assert db_session.query(MatchClaim).filter_by(status="ACTIVE").count() == 0
    entry = db_session.scalars(
        select(AuditLog).where(
            AuditLog.chain_id == MATCHING_CHAIN, AuditLog.action_type == "UNMATCH"
        )
    ).one()
    assert entry.rationale == "wrong customer" and entry.before_state is not None
    assert verify_chain(db_session, MATCHING_CHAIN).ok is True

    # neither exact stage (set-based or Python) may match the pair again
    client.post(
        "/api/v1/reconciliation/run",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json={"bank_code": "HDFC"},
    )
    db_session.refresh(a)
    db_session.refresh(b)
    assert (a.match_status, b.match_status) == ("UNMATCHED", "UNMATCHED")
    assert db_session.query(MatchResult).filter_by(status="AUTO_MATCHED").count() == 0


def test_unmatching_twice_or_with_the_wrong_input_is_refused(
    client: TestClient, db_session: Session
) -> None:
    headers = _key(db_session)
    _, _, match = _exact_pair_matched(client, db_session, headers)
    url = f"/api/v1/matches/{match.id}/unmatch"
    assert client.post(url, headers=headers, json={"reason": "x"}).status_code == 200
    assert client.post(url, headers=headers, json={"reason": "x"}).status_code == 409
    other = f"/api/v1/matches/{uuid.uuid4()}/unmatch"
    assert client.post(other, headers=headers, json={"reason": "x"}).status_code == 404
    assert client.post(url, headers=headers, json={}).status_code == 422
    viewer = _key(db_session, "VIEWER")
    assert client.post(url, headers=viewer, json={"reason": "x"}).status_code == 403


def test_unmatching_one_row_of_a_group_undoes_the_whole_group(
    client: TestClient, db_session: Session
) -> None:
    f = make_ingestion_file(db_session)
    single = make_txn(
        f.id, source="INTERNAL", txn_date=_DAY, amount_minor=100_000, match_status="MATCHED"
    )
    parts = [
        make_txn(f.id, source="EXTERNAL", txn_date=_DAY, amount_minor=m, match_status="MATCHED")
        for m in (40_000, 60_000)
    ]
    db_session.add_all([single, *parts])
    db_session.flush()
    group_id = uuid.uuid4()
    rows = [
        MatchResult(
            run_id="r",
            match_type="SPLIT",
            status="AUTO_MATCHED",
            confidence="0.950",
            match_group_id=group_id,
            internal_transaction_id=single.id,
            external_transaction_id=part.id,
            field_scores={},
            matched_fields={},
            hard_constraints_passed=True,
            candidate_count=2,
            rationale="g",
            matched_on_date=_DAY,
        )
        for part in parts
    ]
    db_session.add_all(rows)
    db_session.flush()

    body = client.post(
        f"/api/v1/matches/{rows[0].id}/unmatch",
        headers=_key(db_session),
        json={"reason": "bad group"},
    ).json()

    assert len(body["match_result_ids"]) == 2
    assert {r.status for r in rows} == {"REJECTED"}
    assert {t.match_status for t in (single, *parts)} == {"UNMATCHED"}


# -- override and reopen ------------------------------------------------------------------------


def _exception(session: Session, **overrides: Any) -> ReconException:
    values: dict[str, Any] = {
        "dedupe_key": uuid.uuid4().hex,
        "category": "MISSING_INTERNAL",
        "severity": "HIGH",
        "status": "OPEN",
        "assigned_tier": 2,
        "assigned_to": "queue:analyst",
        "bank_code": "HDFC",
        "affected_records": {},
        "suggested_resolution": "x",
        "rationale": "y",
        "sla_deadline": _NOW + timedelta(hours=4),
        "created_at": _NOW,
    }
    row = ReconException(**(values | overrides))
    session.add(row)
    session.flush()
    return row


def test_an_admin_can_override_the_tier_and_it_is_audited(
    client: TestClient, db_session: Session
) -> None:
    exc = _exception(db_session)
    response = client.put(
        f"/api/v1/exceptions/{exc.id}/override",
        headers=_key(db_session, "ADMIN"),
        json={"tier": 4, "reason": "regulator query"},
    )
    assert response.status_code == 200
    assert (response.json()["assigned_tier"], response.json()["assigned_to"]) == (
        4,
        "queue:compliance",
    )
    event = db_session.scalars(
        select(ExceptionEvent).where(ExceptionEvent.exception_id == exc.id)
    ).one()
    assert (event.event_type, event.from_tier, event.to_tier) == ("OVERRIDDEN", 2, 4)
    entry = db_session.scalars(
        select(AuditLog).where(
            AuditLog.chain_id == EXCEPTIONS_CHAIN, AuditLog.action_type == "OVERRIDE"
        )
    ).one()
    assert entry.before_state == {"tier": 2, "assigned_to": "queue:analyst"}
    assert entry.rationale == "regulator query"


def test_an_override_keeps_a_named_assignee_and_needs_an_admin(
    client: TestClient, db_session: Session
) -> None:
    exc = _exception(db_session, assigned_to="priya")
    url = f"/api/v1/exceptions/{exc.id}/override"
    body = {"tier": 3, "reason": "x"}
    assert client.put(url, headers=_key(db_session), json=body).status_code == 403
    admin = _key(db_session, "ADMIN")
    assert client.put(url, headers=admin, json=body).json()["assigned_to"] == "priya"
    assert client.put(url, headers=admin, json={"tier": 9, "reason": "x"}).status_code == 422
    exc.status = "RESOLVED"
    db_session.flush()
    assert client.put(url, headers=admin, json=body).status_code == 409
    missing = f"/api/v1/exceptions/{uuid.uuid4()}/override"
    assert client.put(missing, headers=admin, json=body).status_code == 404


def test_a_resolved_exception_can_be_reopened_and_returns_to_a_person(
    client: TestClient, db_session: Session
) -> None:
    exc = _exception(
        db_session,
        status="RESOLVED",
        assigned_tier=1,
        assigned_to=None,
        resolved_by="system:auto-resolver",
        resolved_at=_NOW,
        resolution_details="auto",
    )
    response = client.post(
        f"/api/v1/exceptions/{exc.id}/reopen",
        headers=_key(db_session),
        json={"reason": "the fee was wrong"},
    )
    body = response.json()
    assert response.status_code == 200
    assert (body["status"], body["assigned_tier"], body["assigned_to"]) == (
        "REOPENED",
        2,
        "queue:analyst",
    )
    assert (body["resolved_by"], body["resolution_details"]) == (None, None)
    event = db_session.scalars(
        select(ExceptionEvent).where(ExceptionEvent.exception_id == exc.id)
    ).one()
    assert event.event_type == "REOPENED" and "the fee was wrong" in event.detail


def test_only_a_resolved_exception_can_be_reopened(client: TestClient, db_session: Session) -> None:
    headers = _key(db_session)
    open_exc = _exception(db_session)
    url = f"/api/v1/exceptions/{open_exc.id}/reopen"
    assert client.post(url, headers=headers, json={"reason": "x"}).status_code == 409
    missing = f"/api/v1/exceptions/{uuid.uuid4()}/reopen"
    assert client.post(missing, headers=headers, json={"reason": "x"}).status_code == 404
    viewer = _key(db_session, "VIEWER")
    assert client.post(url, headers=viewer, json={"reason": "x"}).status_code == 403
    assert verify_chain(db_session, EXCEPTIONS_CHAIN).ok is True
