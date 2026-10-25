"""The four reports against seeded data, through the generator and the API."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from recon.api.deps import get_db, get_now
from recon.api.main import app
from recon.audit.chains import EXCEPTIONS_CHAIN
from recon.audit.logger import AuditLogger
from recon.config.registry import default_bank_configs
from recon.domain.enums import AuditActionType
from recon.matching.claims import ClaimsService
from recon.persistence.models import (
    ApiKey,
    MatchResult,
    NormalisedTransaction,
    ReconciliationRun,
    ReconException,
    StatementBalance,
)
from recon.reporting.generator import ReportGenerator
from recon.security.api_keys import generate_api_key, hash_api_key
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)
_DAY = date(2026, 3, 16)


def _generator(session: Session) -> ReportGenerator:
    return ReportGenerator(session, default_bank_configs(), _NOW)


def _exc(session: Session, **overrides: Any) -> ReconException:
    values: dict[str, Any] = {
        "dedupe_key": uuid.uuid4().hex,
        "category": "MISSING_INTERNAL",
        "severity": "HIGH",
        "status": "OPEN",
        "assigned_tier": 2,
        "bank_code": "HDFC",
        "affected_records": {},
        "financial_impact_inr_minor": 100_000,
        "suggested_resolution": "x",
        "rationale": "y",
        "created_at": _NOW - timedelta(minutes=30),
        "sla_deadline": _NOW + timedelta(hours=3),
    }
    row = ReconException(**(values | overrides))
    session.add(row)
    session.flush()
    return row


def _audit_entry(session: Session, rationale: str, *, actor: str = "t") -> None:
    AuditLogger(session).append(
        chain_id=EXCEPTIONS_CHAIN,
        actor_type="SYSTEM",
        actor_id=actor,
        action_type=AuditActionType.RESOLVE,
        affected_records={},
        rationale=rationale,
        occurred_at=_NOW,
    )


def _summary(report: Any) -> dict[str, Any]:
    return dict(report.summary)


def _table(report: Any, name: str) -> Any:
    return next(t for t in report.tables if t.name == name)


# -- daily ---------------------------------------------------------------------------------


def test_the_daily_report_summarises_transactions_exceptions_sla_and_carry_forward(
    db_session: Session,
) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all(
        [
            make_txn(f.id, bank_code="HDFC", txn_date=_DAY, match_status="MATCHED"),
            make_txn(f.id, bank_code="HDFC", txn_date=_DAY, match_status="MATCHED"),
            make_txn(f.id, bank_code="HDFC", txn_date=_DAY, match_status="UNMATCHED"),
            make_txn(f.id, bank_code="ICICI", txn_date=_DAY, match_status="UNMATCHED"),
            make_txn(
                f.id, bank_code="HDFC", txn_date=_DAY - timedelta(days=1), match_status="MATCHED"
            ),
        ]
    )
    db_session.add(
        ReconciliationRun(
            idempotency_key="k1",
            bank_code="HDFC",
            status="COMPLETED",
            requested_by="t",
            created_at=_NOW,
        )
    )
    _exc(db_session)  # today, open, inside its SLA
    _exc(
        db_session,
        status="RESOLVED",
        resolved_at=_NOW,
        resolved_by="system:auto-resolver",
        category="FEE_DEDUCTION",
        severity="LOW",
    )
    _exc(
        db_session,
        sla_deadline=_NOW - timedelta(hours=1),
        sla_breached_at=_NOW - timedelta(minutes=5),
    )  # breached
    _exc(
        db_session, created_at=_NOW - timedelta(days=2), financial_impact_inr_minor=250_000
    )  # carried forward
    db_session.flush()

    report = _generator(db_session).daily(_DAY)

    s = _summary(report)
    assert (s["transactions"], s["matched"], s["match_rate"]) == (4, 2, 0.5)
    assert (s["exceptions_created"], s["exceptions_auto_resolved"], s["exceptions_still_open"]) == (
        3,
        1,
        2,
    )
    assert s["sla_compliance_percent"] == pytest.approx(66.67, abs=0.01)  # 2 of 3 on time
    assert (s["unresolved_carry_forward"], s["carry_forward_value"]) == (1, "₹2,500.00")
    banks = {r[0]: r for r in _table(report, "Per bank").rows}
    assert banks["HDFC"][1:6] == [3, 2, 0.6667, 1, 3]
    assert banks["ICICI"][1] == 1 and banks["AXIS"][1] == 0  # configured banks always appear
    by_category = {(r[0], r[1]): r[2] for r in _table(report, "Exceptions by category").rows}
    assert (
        by_category[("MISSING_INTERNAL", "HIGH")] == 2
        and by_category[("FEE_DEDUCTION", "LOW")] == 1
    )


def test_an_empty_day_reports_zeros_not_errors(db_session: Session) -> None:
    s = _summary(_generator(db_session).daily(date(2026, 1, 1)))
    assert (s["transactions"], s["match_rate"], s["sla_compliance_percent"]) == (0, 0.0, 100.0)


# -- exception ageing -----------------------------------------------------------------------


def test_the_ageing_report_buckets_open_exceptions_with_their_financial_impact(
    db_session: Session,
) -> None:
    _exc(db_session, created_at=_NOW - timedelta(minutes=30), financial_impact_inr_minor=100_000)
    _exc(db_session, created_at=_NOW - timedelta(hours=2), financial_impact_inr_minor=200_000)
    _exc(
        db_session,
        created_at=_NOW - timedelta(hours=10),
        financial_impact_inr_minor=None,
        financial_impact_amount=Decimal("5"),
    )
    _exc(
        db_session,
        created_at=_NOW - timedelta(hours=30),
        financial_impact_inr_minor=400_000,
        bank_code="ICICI",
    )
    _exc(
        db_session,
        created_at=_NOW - timedelta(hours=1),
        status="RESOLVED",
        financial_impact_inr_minor=999_999,
    )

    report = _generator(db_session).exception_ageing()

    rows = {r[0]: r[1:] for r in _table(report, "By age").rows}
    assert rows["< 1 hour"] == [1, "₹1,000.00", 0]
    assert rows["1-4 hours"] == [1, "₹2,000.00", 0]
    assert rows["4-24 hours"] == [1, "₹0.00", 1]  # no INR value: counted, not guessed
    assert rows["> 24 hours"] == [1, "₹4,000.00", 0]
    assert _summary(report)["open_exceptions"] == 4 and _summary(report)["older_than_24_hours"] == 1
    by_bank = {r[0]: r[1:] for r in _table(report, "By bank and age").rows}
    assert by_bank["ICICI"] == [0, 0, 0, 1]
    oldest = _table(report, "Oldest 20 open exceptions").rows
    assert oldest[0][1] == "ICICI" and oldest[0][5] == 30.0


def test_boundaries_belong_to_the_older_bucket(db_session: Session) -> None:
    _exc(db_session, created_at=_NOW - timedelta(hours=1))
    _exc(db_session, created_at=_NOW - timedelta(hours=4))
    _exc(db_session, created_at=_NOW - timedelta(hours=24))
    rows = {r[0]: r[1] for r in _table(_generator(db_session).exception_ageing(), "By age").rows}
    assert rows == {"< 1 hour": 0, "1-4 hours": 1, "4-24 hours": 1, "> 24 hours": 1}


# -- audit compliance --------------------------------------------------------------------------


def _txn(session: Session, status: str = "UNMATCHED") -> NormalisedTransaction:
    f = make_ingestion_file(session)
    txn = make_txn(f.id, txn_date=_DAY, match_status=status)
    session.add(txn)
    session.flush()
    return txn


def test_a_period_where_everything_is_accounted_for_passes(db_session: Session) -> None:
    _txn(db_session, "MATCHED")
    covered = _txn(db_session)
    _exc(db_session, affected_records={"normalised_transaction_ids": [str(covered.id)]})

    report = _generator(db_session).audit_compliance(_DAY, _DAY)

    assert report.verdict == "PASS"
    s = _summary(report)
    assert (s["transactions_in_period"], s["matched"], s["unmatched_without_an_exception"]) == (
        2,
        1,
        0,
    )
    assert s["audit_trail_intact"] is True


def test_an_unmatched_transaction_with_no_exception_fails_the_period(db_session: Session) -> None:
    _txn(db_session)
    report = _generator(db_session).audit_compliance(_DAY, _DAY)
    assert report.verdict == "FAIL"
    assert _summary(report)["unmatched_without_an_exception"] == 1
    assert "1 unmatched transaction(s) have no exception" in _table(report, "Failures").rows[0][0]


@pytest.mark.parametrize(
    "affected",
    [
        lambda t: {"internal_transaction_id": str(t.id)},
        lambda t: {"external_transaction_id": str(t.id)},
        lambda t: {"single_transaction_id": str(t.id)},
        lambda t: {"member_transaction_ids": [str(t.id)]},
    ],
)
def test_every_shape_of_exception_record_counts_as_coverage(
    db_session: Session, affected: Any
) -> None:
    txn = _txn(db_session)
    _exc(db_session, affected_records=affected(txn))
    assert (
        _summary(_generator(db_session).audit_compliance(_DAY, _DAY))[
            "unmatched_without_an_exception"
        ]
        == 0
    )


def test_pairs_held_for_review_and_voided_duplicates_are_not_failures(db_session: Session) -> None:
    held = _txn(db_session)
    ClaimsService(db_session).claim(held.id, "INTERNAL")
    _txn(db_session, "VOIDED")
    report = _generator(db_session).audit_compliance(_DAY, _DAY)
    s = _summary(report)
    assert (s["held_for_review"], s["voided_duplicates"], report.verdict) == (1, 1, "PASS")


def test_an_exception_past_its_sla_that_was_never_escalated_fails_the_period(
    db_session: Session,
) -> None:
    _exc(db_session, sla_deadline=_NOW - timedelta(hours=1), sla_breached_at=None)
    report = _generator(db_session).audit_compliance(_DAY, _DAY)
    assert report.verdict == "FAIL" and _summary(report)["exceptions_past_sla_not_escalated"] == 1


def test_a_tampered_audit_entry_fails_the_period(db_session: Session) -> None:
    _audit_entry(db_session, "original")
    db_session.execute(
        text("ALTER TABLE audit.audit_log DISABLE TRIGGER audit_log_forbid_mutation")
    )
    db_session.execute(text("UPDATE audit.audit_log SET rationale = 'edited'"))
    db_session.execute(text("ALTER TABLE audit.audit_log ENABLE TRIGGER audit_log_forbid_mutation"))

    report = _generator(db_session).audit_compliance(_DAY, _DAY)

    assert report.verdict == "FAIL" and _summary(report)["audit_trail_intact"] is False
    chains = {r[0]: r[2] for r in _table(report, "Audit chains").rows}
    assert "BROKEN" in chains[EXCEPTIONS_CHAIN]


def test_a_failed_file_check_or_unbalanced_statement_fails_the_period(db_session: Session) -> None:
    f = make_ingestion_file(db_session, integrity_status="TAMPERED", ingested_at=_NOW)
    db_session.add(
        StatementBalance(
            ingestion_file_id=f.id,
            bank_code="SBI",
            currency="INR",
            opening_minor=0,
            closing_minor=10,
            computed_closing_minor=0,
            net_entries_minor=0,
            difference_minor=10,
            entry_count=0,
            balanced=False,
            created_at=_NOW,
        )
    )
    db_session.flush()
    report = _generator(db_session).audit_compliance(_DAY, _DAY)
    s = _summary(report)
    assert (report.verdict, s["files_failing_reverification"], s["statements_not_balancing"]) == (
        "FAIL",
        1,
        1,
    )


# -- recovery ------------------------------------------------------------------


def test_the_recovery_report_projects_the_time_to_clear_the_backlog(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    internal = make_txn(f.id, source="INTERNAL", txn_date=_DAY, match_status="MATCHED")
    external = make_txn(f.id, source="EXTERNAL", txn_date=_DAY, match_status="MATCHED")
    db_session.add_all([internal, external])
    db_session.flush()
    for i, hours_ago in enumerate((1, 1, 2, 5)):
        db_session.add(
            MatchResult(
                run_id="r",
                match_type="EXACT",
                status="AUTO_MATCHED",
                confidence=Decimal("1.000"),
                internal_transaction_id=internal.id,
                external_transaction_id=external.id,
                field_scores={},
                matched_fields={},
                hard_constraints_passed=True,
                candidate_count=1,
                rationale=f"m{i}",
                matched_on_date=_DAY,
                created_at=_NOW - timedelta(hours=hours_ago, minutes=10),
            )
        )
    for _ in range(12):  # twelve unmatched transactions are the backlog
        db_session.add(make_txn(f.id, bank_code="HDFC", txn_date=_DAY, match_status="UNMATCHED"))
    db_session.flush()

    report = _generator(db_session).recovery(window_hours=24)

    s = _summary(report)
    assert s["backlog_unmatched_transactions"] == 12
    assert s["reconciled_in_window"] == 4 and s["reconciled_per_hour"] == pytest.approx(
        0.2, abs=0.05
    )
    assert s["projected_hours_to_clear"] == pytest.approx(12 / (4 / 24), abs=0.1)
    hours = _table(report, "Reconciled per hour").rows
    assert sum(r[1] for r in hours) == 4 and len(hours) == 3
    assert _table(report, "Backlog by bank").rows == [["HDFC", 12]]


def test_with_nothing_matched_the_projection_is_unknown_not_infinite(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(make_txn(f.id, match_status="UNMATCHED"))
    db_session.flush()
    assert _summary(_generator(db_session).recovery())["projected_hours_to_clear"] is None


def test_the_estimate_applies_the_historical_exception_rate(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    for _ in range(10):
        db_session.add(make_txn(f.id, match_status="UNMATCHED"))
    _exc(db_session)
    _exc(db_session)  # 2 exceptions / 10 transactions = 20%
    db_session.flush()
    s = _summary(_generator(db_session).recovery())
    assert (s["historical_exception_rate"], s["estimated_exceptions_from_backlog"]) == (0.2, 2)


# -- the API -------------------------------------------------------------------


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_now] = lambda: _NOW
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _headers(session: Session, role: str) -> dict[str, str]:
    raw = generate_api_key()
    session.add(ApiKey(key_hash=hash_api_key(raw), name=f"k-{uuid.uuid4().hex[:8]}", role=role))
    session.flush()
    return {"X-API-Key": raw}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/reports/daily?date=2026-03-16",
        "/api/v1/reports/exception-ageing",
        "/api/v1/reports/audit-compliance?date_from=2026-03-16&date_to=2026-03-16",
        "/api/v1/reports/recovery",
    ],
)
def test_every_report_comes_as_json_html_and_csv(
    client: TestClient, db_session: Session, path: str
) -> None:
    h = _headers(db_session, "ANALYST")
    sep = "&" if "?" in path else "?"
    as_json = client.get(path, headers=h)
    as_html = client.get(f"{path}{sep}format=html", headers=h)
    as_csv = client.get(f"{path}{sep}format=csv", headers=h)

    assert as_json.status_code == as_html.status_code == as_csv.status_code == 200
    body = as_json.json()
    assert body["title"] and isinstance(body["tables"], list) and body["generated_at"]
    assert as_html.headers["content-type"].startswith("text/html") and "<table" in as_html.text
    assert as_csv.headers["content-type"].startswith("text/csv")
    assert as_csv.headers["content-disposition"].startswith("attachment")
    assert next(iter(csv.reader(io.StringIO(as_csv.text))))[0].startswith("# ")


def test_the_audit_compliance_verdict_is_in_the_json(
    client: TestClient, db_session: Session
) -> None:
    body = client.get(
        "/api/v1/reports/audit-compliance?date_from=2026-03-16",
        headers=_headers(db_session, "ANALYST"),
    ).json()
    assert body["verdict"] == "PASS" and body["parameters"] == {
        "date_from": "2026-03-16",
        "date_to": "2026-03-16",
    }


def test_report_parameters_are_validated(client: TestClient, db_session: Session) -> None:
    h = _headers(db_session, "ANALYST")
    assert (
        client.get(
            "/api/v1/reports/audit-compliance?date_from=2026-03-17&date_to=2026-03-16", headers=h
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/reports/audit-compliance?date_from=2024-01-01&date_to=2026-03-16", headers=h
        ).status_code
        == 422
    )
    assert client.get("/api/v1/reports/recovery?window_hours=0", headers=h).status_code == 422
    assert client.get("/api/v1/reports/daily?format=pdf", headers=h).status_code == 422


def test_reports_need_an_analyst(client: TestClient, db_session: Session) -> None:
    assert (
        client.get("/api/v1/reports/daily", headers=_headers(db_session, "VIEWER")).status_code
        == 403
    )


def test_the_exceptions_export_is_csv_with_the_filters_and_neutralised_formulas(
    client: TestClient, db_session: Session
) -> None:
    _exc(db_session, rationale='=HYPERLINK("http://evil")', category="FEE_DEDUCTION")
    _exc(db_session, category="MISSING_INTERNAL")
    response = client.get(
        "/api/v1/exceptions/export?category=FEE_DEDUCTION", headers=_headers(db_session, "VIEWER")
    )
    rows = list(csv.reader(io.StringIO(response.text)))
    assert response.headers["content-type"].startswith("text/csv")
    assert rows[0][:3] == ["id", "category", "severity"] and len(rows) == 2
    assert rows[1][-1].startswith("'=HYPERLINK")


def test_the_audit_export_filters_and_anchors_detect_tail_truncation(
    client: TestClient, db_session: Session
) -> None:
    for n in range(3):
        _audit_entry(db_session, f"entry {n}", actor="bot")
    viewer = _headers(db_session, "VIEWER")

    exported = list(
        csv.reader(
            io.StringIO(
                client.get(
                    "/api/v1/audit/export?chain_id=exceptions&actor_id=bot", headers=viewer
                ).text
            )
        )
    )
    assert [r[1] for r in exported[1:]] == ["1", "2", "3"]
    assert (
        len(client.get("/api/v1/audit?date_from=2026-03-17", headers=viewer).json()["items"]) == 0
    )
    assert (
        client.get("/api/v1/audit?actor_id=bot&date_to=2026-03-16", headers=viewer).json()["total"]
        == 3
    )

    anchors = client.get("/api/v1/audit/anchors", headers=viewer).json()["anchors"]
    head = next(a for a in anchors if a["chain_id"] == EXCEPTIONS_CHAIN)
    assert head["sequence_no"] == 3
    token = f"{head['chain_id']}:{head['sequence_no']}:{head['current_hash']}"
    assert (
        client.get("/api/v1/audit/verify", params={"expected": token}, headers=viewer).json()["ok"]
        is True
    )

    db_session.execute(
        text("ALTER TABLE audit.audit_log DISABLE TRIGGER audit_log_forbid_mutation")
    )
    db_session.execute(
        text("DELETE FROM audit.audit_log WHERE chain_id = 'exceptions' AND sequence_no = 3")
    )
    db_session.execute(text("ALTER TABLE audit.audit_log ENABLE TRIGGER audit_log_forbid_mutation"))
    assert (
        client.get("/api/v1/audit/verify", headers=viewer).json()["ok"] is True
    )  # a hash walk alone is blind to it
    truncated = client.get(
        "/api/v1/audit/verify", params={"expected": token}, headers=viewer
    ).json()
    assert (
        truncated["ok"] is False
        and truncated["chains"][EXCEPTIONS_CHAIN]["failure_kind"] == "HEAD_MISMATCH"
    )
    assert client.get("/api/v1/audit/verify?expected=garbage", headers=viewer).status_code == 422
