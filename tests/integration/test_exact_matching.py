# tests/integration/test_exact_matching.py
"""Integration tests for ExactMatchingStrategy.

test_exact_match_is_recorded_and_both_sides_claimed is the primary
end-to-end proof: two independently-created NormalisedTransaction rows
(one INTERNAL, one EXTERNAL) sharing the same normalised reference,
amount, currency and direction produce exactly one MatchResult and both
transactions end up MATCHED with ACTIVE claims pointing at that result.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from recon.matching.claims import ClaimsService
from recon.matching.strategies.exact import ExactMatchingStrategy
from recon.persistence.models import IngestionFile, MatchClaim, MatchResult, NormalisedTransaction


def _make_ingestion_file(session: Session, **overrides: object) -> IngestionFile:
    defaults: dict[str, object] = {
        "bank_code": "HDFC",
        "format_type": "CSV",
        "original_filename": "test.csv",
        "content_sha256": uuid.uuid4().hex + uuid.uuid4().hex,  # 64 hex chars, unique per call
        "size_bytes": 100,
        "ingested_by": "test-suite",
    }
    defaults.update(overrides)
    ingestion_file = IngestionFile(**defaults)
    session.add(ingestion_file)
    session.flush()
    return ingestion_file


def _make_txn(ingestion_file_id: uuid.UUID, **overrides: object) -> NormalisedTransaction:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "txn_date": date(2026, 3, 15),
        "raw_transaction_id": uuid.uuid4(),
        "ingestion_file_id": ingestion_file_id,
        "source": "INTERNAL",
        "bank_code": "HDFC",
        "format_type": "CSV",
        "config_version": "hdfc.v1",
        "txn_id": "REF001",
        "amount": Decimal("1500.00"),
        "amount_minor": 150_000,
        "currency": "INR",
        "currency_exponent": 2,
        "currency_source": "ENTRY_LEVEL",
        "direction": "CR",
        "is_reversal": False,
        "txn_timestamp_utc": datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        "txn_timestamp_original": "15-03-2026",
        "source_timezone": "Asia/Kolkata",
        "normalised_reference": "REF001",
        "match_status": "UNMATCHED",
    }
    defaults.update(overrides)
    return NormalisedTransaction(**defaults)


def test_exact_match_is_recorded_and_both_sides_claimed(db_session: Session) -> None:
    ingestion_file = _make_ingestion_file(db_session)
    internal_txn = _make_txn(ingestion_file.id, source="INTERNAL")
    external_txn = _make_txn(
        ingestion_file.id, source="EXTERNAL", id=uuid.uuid4(), raw_transaction_id=uuid.uuid4()
    )
    db_session.add_all([internal_txn, external_txn])
    db_session.flush()

    strategy = ExactMatchingStrategy(db_session, run_id="test-run-1")
    outcome = strategy.run([internal_txn], [external_txn])

    assert outcome.matched_count == 1
    assert outcome.skipped_no_key_count == 0
    assert outcome.skipped_claim_conflict_count == 0
    assert len(outcome.match_result_ids) == 1

    match_result = db_session.get(MatchResult, outcome.match_result_ids[0])
    assert match_result is not None
    assert match_result.match_type == "EXACT"
    assert match_result.status == "AUTO_MATCHED"
    assert match_result.confidence == Decimal("1.000")
    assert match_result.internal_transaction_id == internal_txn.id
    assert match_result.external_transaction_id == external_txn.id

    assert internal_txn.match_status == "MATCHED"
    assert external_txn.match_status == "MATCHED"

    claims = (
        db_session.query(MatchClaim).filter(MatchClaim.match_result_id == match_result.id).all()
    )
    assert len(claims) == 2
    assert {c.role for c in claims} == {"INTERNAL", "EXTERNAL"}
    assert all(c.status == "ACTIVE" for c in claims)


def test_no_candidate_with_matching_key_is_left_unmatched(db_session: Session) -> None:
    ingestion_file = _make_ingestion_file(db_session)
    internal_txn = _make_txn(ingestion_file.id, source="INTERNAL", normalised_reference="REF001")
    external_txn = _make_txn(
        ingestion_file.id,
        source="EXTERNAL",
        id=uuid.uuid4(),
        raw_transaction_id=uuid.uuid4(),
        normalised_reference="REF999",
    )
    db_session.add_all([internal_txn, external_txn])
    db_session.flush()

    strategy = ExactMatchingStrategy(db_session, run_id="test-run-2")
    outcome = strategy.run([internal_txn], [external_txn])

    assert outcome.matched_count == 0
    assert internal_txn.match_status == "UNMATCHED"
    assert external_txn.match_status == "UNMATCHED"


def test_transaction_with_no_normalised_reference_is_skipped_not_crashed(
    db_session: Session,
) -> None:
    ingestion_file = _make_ingestion_file(db_session)
    internal_txn = _make_txn(ingestion_file.id, source="INTERNAL", normalised_reference=None)
    external_txn = _make_txn(
        ingestion_file.id, source="EXTERNAL", id=uuid.uuid4(), raw_transaction_id=uuid.uuid4()
    )
    db_session.add_all([internal_txn, external_txn])
    db_session.flush()

    strategy = ExactMatchingStrategy(db_session, run_id="test-run-3")
    outcome = strategy.run([internal_txn], [external_txn])

    assert outcome.matched_count == 0
    assert outcome.skipped_no_key_count == 1


def test_hash_collision_disambiguated_by_closest_txn_date(db_session: Session) -> None:
    ingestion_file = _make_ingestion_file(db_session)
    close_internal = _make_txn(
        ingestion_file.id,
        source="INTERNAL",
        txn_date=date(2026, 3, 15),
        raw_transaction_id=uuid.uuid4(),
    )
    far_internal = _make_txn(
        ingestion_file.id,
        source="INTERNAL",
        id=uuid.uuid4(),
        txn_date=date(2026, 3, 1),
        raw_transaction_id=uuid.uuid4(),
    )
    external_txn = _make_txn(
        ingestion_file.id,
        source="EXTERNAL",
        id=uuid.uuid4(),
        raw_transaction_id=uuid.uuid4(),
        txn_date=date(2026, 3, 16),
    )
    db_session.add_all([close_internal, far_internal, external_txn])
    db_session.flush()

    strategy = ExactMatchingStrategy(db_session, run_id="test-run-4")
    outcome = strategy.run([close_internal, far_internal], [external_txn])

    assert outcome.matched_count == 1
    match_result = db_session.get(MatchResult, outcome.match_result_ids[0])
    assert match_result is not None
    assert match_result.internal_transaction_id == close_internal.id
    assert match_result.candidate_count == 2

    assert close_internal.match_status == "MATCHED"
    assert far_internal.match_status == "UNMATCHED"


def test_already_claimed_internal_transaction_is_skipped_not_double_matched(
    db_session: Session,
) -> None:
    ingestion_file = _make_ingestion_file(db_session)
    internal_txn = _make_txn(ingestion_file.id, source="INTERNAL")
    external_txn = _make_txn(
        ingestion_file.id, source="EXTERNAL", id=uuid.uuid4(), raw_transaction_id=uuid.uuid4()
    )
    db_session.add_all([internal_txn, external_txn])
    db_session.flush()

    ClaimsService(db_session).claim(internal_txn.id, "INTERNAL")

    strategy = ExactMatchingStrategy(db_session, run_id="test-run-5")
    outcome = strategy.run([internal_txn], [external_txn])

    assert outcome.matched_count == 0
    assert outcome.skipped_claim_conflict_count == 1
    assert external_txn.match_status == "UNMATCHED"
