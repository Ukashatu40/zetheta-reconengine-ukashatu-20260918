# tests/integration/test_orchestrator.py
"""Integration tests for MatchingOrchestrator.

test_transaction_matched_by_exact_is_never_offered_to_fuzzy is the
load-bearing proof of invariant I2 (monotonic strength). It doesn't
assert anything about the ORCHESTRATOR's code directly — it constructs a
scenario where, if the re-fetch-before-fuzzy behaviour were broken (e.g.
someone "optimised" the orchestrator to reuse the pre-exact pool for
fuzzy too), a transaction already matched exactly would end up being
reconsidered by fuzzy matching and this test would fail.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from recon.matching.blocking.candidates import BlockingConfig
from recon.matching.orchestrator import MatchingOrchestrator
from recon.persistence.models import IngestionFile, MatchResult, NormalisedTransaction


def _make_ingestion_file(session: Session, **overrides: object) -> IngestionFile:
    defaults: dict[str, object] = {
        "bank_code": "HDFC",
        "format_type": "CSV",
        "original_filename": "test.csv",
        "content_sha256": uuid.uuid4().hex + uuid.uuid4().hex,
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
        "counterparty_name_normalised": None,
        "match_status": "UNMATCHED",
    }
    defaults.update(overrides)
    return NormalisedTransaction(**defaults)


def _blocking_config() -> BlockingConfig:
    return BlockingConfig(amount_bucket_width_minor=10_000, date_window_days=2)


def test_exact_match_is_found_first_and_fuzzy_never_sees_it(db_session: Session) -> None:
    """Two transactions that would ALSO be fuzzy-matchable (same
    reference, so they'd trivially fuzzy-match too) must be resolved by
    the EXACT level and never reach fuzzy scoring at all."""
    ingestion_file = _make_ingestion_file(db_session)
    internal_txn = _make_txn(ingestion_file.id, source="INTERNAL")
    external_txn = _make_txn(
        ingestion_file.id, source="EXTERNAL", id=uuid.uuid4(), raw_transaction_id=uuid.uuid4()
    )
    db_session.add_all([internal_txn, external_txn])
    db_session.flush()

    orchestrator = MatchingOrchestrator(
        db_session,
        run_id="test-run-1",
        blocking_config=_blocking_config(),
        amount_tolerance_minor=100,
    )
    outcome = orchestrator.run(bank_code="HDFC")

    assert outcome.exact.matched_count == 1
    assert outcome.fuzzy.auto_matched_count == 0
    assert outcome.fuzzy.review_count == 0

    # The critical proof: fuzzy's OWN pool (fetched after exact ran) was
    # already empty, meaning it never even considered these transactions.
    assert outcome.internal_pool_size_before_fuzzy == 0
    assert outcome.external_pool_size_before_fuzzy == 0

    match_results = db_session.query(MatchResult).all()
    assert len(match_results) == 1
    assert match_results[0].match_type == "EXACT"


def test_transactions_exact_cannot_match_fall_through_to_fuzzy(db_session: Session) -> None:
    """A pair that exact matching cannot resolve (references differ
    slightly, so the exact hash key never collides) but that fuzzy
    matching CAN resolve, must actually be matched by the fuzzy level —
    proving levels compose, not just that exact runs first in isolation."""
    ingestion_file = _make_ingestion_file(db_session)
    internal_txn = _make_txn(
        ingestion_file.id, source="INTERNAL", normalised_reference="REF0001234567"
    )
    external_txn = _make_txn(
        ingestion_file.id,
        source="EXTERNAL",
        id=uuid.uuid4(),
        raw_transaction_id=uuid.uuid4(),
        normalised_reference="REF0001234568",  # one character different -> exact key differs
        counterparty_name_normalised=None,
    )
    db_session.add_all([internal_txn, external_txn])
    db_session.flush()

    orchestrator = MatchingOrchestrator(
        db_session,
        run_id="test-run-2",
        blocking_config=_blocking_config(),
        amount_tolerance_minor=100,
    )
    outcome = orchestrator.run(bank_code="HDFC")

    assert outcome.exact.matched_count == 0
    assert outcome.internal_pool_size_before_fuzzy == 1  # exact left it unmatched
    assert outcome.external_pool_size_before_fuzzy == 1
    assert outcome.fuzzy.auto_matched_count == 1  # fuzzy resolved what exact couldn't

    match_results = db_session.query(MatchResult).all()
    assert len(match_results) == 1
    assert match_results[0].match_type == "FUZZY"


def test_unmatchable_transactions_remain_unmatched_after_both_levels(db_session: Session) -> None:
    ingestion_file = _make_ingestion_file(db_session)
    internal_txn = _make_txn(
        ingestion_file.id,
        source="INTERNAL",
        normalised_reference="TOTALLYUNRELATED",
        amount_minor=999_999_999,
        txn_date=date(2020, 1, 1),
    )
    external_txn = _make_txn(
        ingestion_file.id, source="EXTERNAL", id=uuid.uuid4(), raw_transaction_id=uuid.uuid4()
    )
    db_session.add_all([internal_txn, external_txn])
    db_session.flush()

    orchestrator = MatchingOrchestrator(
        db_session,
        run_id="test-run-3",
        blocking_config=_blocking_config(),
        amount_tolerance_minor=100,
    )
    outcome = orchestrator.run(bank_code="HDFC")

    assert outcome.total_matched_count == 0
    assert internal_txn.match_status == "UNMATCHED"
    assert external_txn.match_status == "UNMATCHED"


def test_orchestrator_is_scoped_to_a_single_bank_code(db_session: Session) -> None:
    """A transaction from a DIFFERENT bank_code must not be pulled into
    this run's pool at all, even if it would otherwise be a trivial exact
    match — confirms MatchingRepository.find_unmatched's bank_code filter
    is actually respected end to end through the orchestrator."""
    ingestion_file = _make_ingestion_file(db_session)
    other_bank_ingestion_file = _make_ingestion_file(db_session, bank_code="ICICI")

    hdfc_internal = _make_txn(ingestion_file.id, source="INTERNAL", bank_code="HDFC")
    icici_internal = _make_txn(
        other_bank_ingestion_file.id,
        source="INTERNAL",
        bank_code="ICICI",
        id=uuid.uuid4(),
        raw_transaction_id=uuid.uuid4(),
    )
    hdfc_external = _make_txn(
        ingestion_file.id,
        source="EXTERNAL",
        bank_code="HDFC",
        id=uuid.uuid4(),
        raw_transaction_id=uuid.uuid4(),
    )
    db_session.add_all([hdfc_internal, icici_internal, hdfc_external])
    db_session.flush()

    orchestrator = MatchingOrchestrator(
        db_session,
        run_id="test-run-4",
        blocking_config=_blocking_config(),
        amount_tolerance_minor=100,
    )
    outcome = orchestrator.run(bank_code="HDFC")

    assert outcome.internal_pool_size_before_exact == 1  # only the HDFC internal txn
    assert outcome.exact.matched_count == 1
    assert icici_internal.match_status == "UNMATCHED"  # untouched by this run entirely
