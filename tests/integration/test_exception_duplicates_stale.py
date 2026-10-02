# tests/integration/test_exception_duplicates_stale.py
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from recon.audit.logger import AuditLogger
from recon.excmgmt.classifier import ExceptionClassifier
from recon.excmgmt.taxonomy import load_taxonomy
from recon.persistence.models import ReconException
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
_EARLY = datetime(2026, 3, 1, tzinfo=UTC)
_LATE = datetime(2026, 3, 2, tzinfo=UTC)


def _classifier(session: Session) -> ExceptionClassifier:
    return ExceptionClassifier(session, load_taxonomy(), AuditLogger(session))


def _categories(session: Session) -> list[str]:
    return sorted(e.category for e in session.query(ReconException).all())


def test_second_identical_internal_is_a_duplicate_and_the_first_is_the_original(
    db_session: Session,
) -> None:
    f = make_ingestion_file(db_session, ingested_at=_EARLY)
    original = make_txn(f.id, source="INTERNAL", source_line_no=1)
    duplicate = make_txn(f.id, source="INTERNAL", source_line_no=2)
    db_session.add_all([original, duplicate])
    db_session.flush()

    _classifier(db_session).classify_unmatched("HDFC", "run-1", _NOW)

    exc = (
        db_session.query(ReconException)
        .filter(ReconException.category == "DUPLICATE_INTERNAL")
        .one()
    )
    assert exc.affected_records["normalised_transaction_ids"] == [str(duplicate.id)]
    assert exc.affected_records["original_transaction_id"] == str(original.id)
    assert exc.affected_records["same_ingestion_file"] is True
    assert exc.assigned_tier == 1
    assert _categories(db_session) == [
        "DUPLICATE_INTERNAL",
        "MISSING_EXTERNAL",
    ]  # the original is still unmatched


def test_duplicate_external_in_a_later_file_is_found_even_when_both_are_matched(
    db_session: Session,
) -> None:
    """B4.4: each copy matched a different internal, so the match rate hid it."""
    early = make_ingestion_file(db_session, ingested_at=_EARLY)
    late = make_ingestion_file(db_session, ingested_at=_LATE)
    original = make_txn(early.id, source="EXTERNAL", source_line_no=1, match_status="MATCHED")
    duplicate = make_txn(late.id, source="EXTERNAL", source_line_no=1, match_status="MATCHED")
    db_session.add_all([original, duplicate])
    db_session.flush()

    _classifier(db_session).classify_unmatched("HDFC", "run-1", _NOW)

    exc = db_session.query(ReconException).one()  # exactly one: nothing is "missing"
    assert exc.category == "DUPLICATE_EXTERNAL"
    assert exc.assigned_tier == 2
    assert exc.affected_records["original_transaction_id"] == str(original.id)
    assert exc.affected_records["same_ingestion_file"] is False


def test_same_reference_on_a_different_date_is_not_a_duplicate(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all(
        [
            make_txn(f.id, source="EXTERNAL", txn_date=date(2026, 3, 15)),
            make_txn(f.id, source="EXTERNAL", txn_date=date(2026, 3, 16)),
        ]
    )
    db_session.flush()

    _classifier(db_session).classify_unmatched("HDFC", "run-1", _NOW)

    assert _categories(db_session) == ["MISSING_INTERNAL", "MISSING_INTERNAL"]


def test_a_duplicate_does_not_block_its_original_from_being_paired(db_session: Session) -> None:
    f = make_ingestion_file(db_session, ingested_at=_EARLY)
    original = make_txn(f.id, source="INTERNAL", source_line_no=1)
    duplicate = make_txn(f.id, source="INTERNAL", source_line_no=2)
    external = make_txn(f.id, source="EXTERNAL", amount_minor=150_500)
    db_session.add_all([original, duplicate, external])
    db_session.flush()

    _classifier(db_session).classify_unmatched("HDFC", "run-1", _NOW)

    assert _categories(db_session) == ["AMOUNT_MISMATCH", "DUPLICATE_INTERNAL"]
    amount = (
        db_session.query(ReconException).filter(ReconException.category == "AMOUNT_MISMATCH").one()
    )
    assert amount.affected_records["internal_transaction_id"] == str(original.id)


def test_reclassifying_creates_no_new_duplicate_exceptions(db_session: Session) -> None:
    f = make_ingestion_file(db_session, ingested_at=_EARLY)
    db_session.add_all(
        [
            make_txn(f.id, source="INTERNAL", source_line_no=1),
            make_txn(f.id, source="INTERNAL", source_line_no=2),
        ]
    )
    db_session.flush()
    classifier = _classifier(db_session)

    first = classifier.classify_unmatched("HDFC", "run-1", _NOW)
    second = classifier.classify_unmatched("HDFC", "run-2", _NOW)

    assert (first.created_count, second.created_count) == (2, 0)


@pytest.mark.parametrize(
    ("age_days", "category"), [(1, "MISSING_INTERNAL"), (2, "STALE_TRANSACTION")]
)
def test_stale_only_when_strictly_older_than_the_window(
    db_session: Session, age_days: int, category: str
) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(
        make_txn(f.id, source="EXTERNAL", txn_date=date(2026, 3, 15) - timedelta(days=age_days))
    )
    db_session.flush()

    _classifier(db_session).classify_unmatched(
        "HDFC", "run-1", _NOW, as_of_date=date(2026, 3, 15), stale_after_days=1
    )

    exc = db_session.query(ReconException).one()
    assert exc.category == category
    if category == "STALE_TRANSACTION":
        assert exc.sla_deadline == _NOW + timedelta(minutes=1440)
        assert exc.affected_records["missing_side"] == "MISSING_INTERNAL"


def test_stale_parameters_must_be_given_together(db_session: Session) -> None:
    with pytest.raises(ValueError, match="together"):
        _classifier(db_session).classify_unmatched(
            "HDFC", "run-1", _NOW, as_of_date=date(2026, 3, 15)
        )
