# tests/integration/test_exception_classifier.py
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from recon.audit.logger import AuditLogger
from recon.excmgmt.classifier import ExceptionClassifier
from recon.excmgmt.taxonomy import load_taxonomy
from recon.matching.claims import ClaimsService
from recon.persistence.models import ExceptionEvent, RawTransaction, ReconException
from recon.persistence.models.normalised import NormalisedTransaction
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _classifier(session: Session) -> ExceptionClassifier:
    return ExceptionClassifier(session, load_taxonomy(), AuditLogger(session))


def _ext(file_id: uuid.UUID, **kw: object) -> NormalisedTransaction:
    return make_txn(file_id, source="EXTERNAL", **kw)


def _int(file_id: uuid.UUID, **kw: object) -> NormalisedTransaction:
    return make_txn(file_id, source="INTERNAL", **kw)


def _classify(session: Session, *txns: object) -> ReconException | None:
    session.add_all(list(txns))
    session.flush()
    _classifier(session).classify_unmatched("HDFC", "run-1", _NOW)
    return session.query(ReconException).one_or_none()


def test_unmatched_external_is_missing_internal(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    exc = _classify(db_session, _ext(f.id))
    assert exc is not None
    assert exc.category == "MISSING_INTERNAL"
    assert (exc.assigned_tier, exc.severity, exc.status) == (2, "HIGH", "OPEN")
    assert exc.sla_deadline == _NOW + timedelta(minutes=240)
    events = db_session.query(ExceptionEvent).filter(ExceptionEvent.exception_id == exc.id).all()
    assert [e.event_type for e in events] == ["CREATED"]


def test_unmatched_internal_is_missing_external(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    exc = _classify(db_session, _int(f.id))
    assert exc is not None and exc.category == "MISSING_EXTERNAL"


def test_opposite_direction_same_amount_is_direction_reversal_tier_4(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    exc = _classify(db_session, _int(f.id, direction="CR"), _ext(f.id, direction="DR"))
    assert exc is not None  # one exception, not two MISSING ones
    assert exc.category == "DIRECTION_REVERSAL"
    assert (exc.assigned_tier, exc.severity) == (4, "CRITICAL")
    assert exc.sla_deadline == _NOW + timedelta(minutes=30)  # AE-03


def test_different_currency_is_currency_mismatch(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    exc = _classify(db_session, _int(f.id), _ext(f.id, currency="USD"))
    assert exc is not None and exc.category == "CURRENCY_MISMATCH"


@pytest.mark.parametrize(("diff", "tier"), [(1, 1), (500, 2)])
def test_amount_mismatch_tier_depends_on_difference(
    db_session: Session, diff: int, tier: int
) -> None:
    f = make_ingestion_file(db_session)
    exc = _classify(db_session, _int(f.id), _ext(f.id, amount_minor=150_000 + diff))
    assert exc is not None and exc.category == "AMOUNT_MISMATCH"
    assert exc.assigned_tier == tier


@pytest.mark.parametrize(("days", "tier"), [(1, 1), (2, 2)])
def test_date_mismatch_tier_depends_on_offset(db_session: Session, days: int, tier: int) -> None:
    f = make_ingestion_file(db_session)
    exc = _classify(
        db_session, _int(f.id), _ext(f.id, txn_date=date(2026, 3, 15) + timedelta(days=days))
    )
    assert exc is not None and exc.category == "DATE_MISMATCH"
    assert exc.assigned_tier == tier


def test_classifying_twice_creates_nothing_the_second_time(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all([_int(f.id, normalised_reference="A"), _ext(f.id, normalised_reference="B")])
    db_session.flush()
    classifier = _classifier(db_session)

    first = classifier.classify_unmatched("HDFC", "run-1", _NOW)
    second = classifier.classify_unmatched("HDFC", "run-2", _NOW)

    assert (first.created_count, second.created_count) == (2, 0)
    assert second.skipped_existing_count == 2
    assert db_session.query(ReconException).count() == 2


def test_transaction_held_by_an_active_claim_is_not_missing(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    held = _ext(f.id)
    db_session.add(held)
    db_session.flush()
    ClaimsService(db_session).claim(held.id, "EXTERNAL")

    _classifier(db_session).classify_unmatched("HDFC", "run-1", _NOW)

    assert db_session.query(ReconException).count() == 0


@pytest.mark.parametrize(("count", "tier"), [(50, 2), (51, 4)])
def test_systemic_failure_is_strictly_more_than_fifty(
    db_session: Session, count: int, tier: int
) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all([_ext(f.id, normalised_reference=f"R{i:03d}") for i in range(count)])
    db_session.flush()

    _classifier(db_session).classify_unmatched("HDFC", "run-1", _NOW)

    tiers = {e.assigned_tier for e in db_session.query(ReconException).all()}
    assert tiers == {tier}


def test_value_above_ten_lakh_is_tier_3_and_critical(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    exc = _classify(db_session, _ext(f.id, amount_minor=110_000_000, amount=Decimal("1100000.00")))
    assert exc is not None
    assert (exc.assigned_tier, exc.severity) == (3, "CRITICAL")


def test_non_inr_value_is_not_evaluated_against_inr_thresholds(db_session: Session) -> None:
    """DD-10: no FX rates yet, so a huge USD amount stays at the category default."""
    f = make_ingestion_file(db_session)
    exc = _classify(db_session, _ext(f.id, currency="USD", amount_minor=900_000_000))
    assert exc is not None and exc.assigned_tier == 2


def _raw(file_id: uuid.UUID, line: int, status: str = "QUARANTINED") -> RawTransaction:
    return RawTransaction(
        ingested_on=date(2026, 3, 15),
        ingestion_file_id=file_id,
        bank_code="HDFC",
        format_type="CSV",
        source_line_no=line,
        raw_payload={},
        parse_status=status,
        parse_errors=(
            {"errors": [{"code": "INVALID_AMOUNT", "field": "amount", "detail": "x"}]}
            if status == "QUARANTINED"
            else None
        ),
    )


def test_quarantined_rows_become_format_errors_and_parsed_rows_are_ignored(
    db_session: Session,
) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all([_raw(f.id, 2), _raw(f.id, 3), _raw(f.id, 4, status="PARSED")])
    db_session.flush()

    outcome = _classifier(db_session).classify_ingestion_file(f.id, _NOW)

    assert outcome.created_by_category == {"FORMAT_ERROR": 2}
    exc = db_session.query(ReconException).first()
    assert exc is not None
    assert (exc.assigned_tier, exc.sla_deadline) == (2, _NOW + timedelta(minutes=60))
    assert "INVALID_AMOUNT" in exc.rationale


def test_many_quarantined_rows_collapse_into_one_systemic_exception(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all([_raw(f.id, line) for line in range(2, 53)])  # 51 rows
    db_session.flush()

    _classifier(db_session).classify_ingestion_file(f.id, _NOW)

    exc = db_session.query(ReconException).one()
    assert exc.assigned_tier == 4
    assert exc.affected_records["total_count"] == 51
