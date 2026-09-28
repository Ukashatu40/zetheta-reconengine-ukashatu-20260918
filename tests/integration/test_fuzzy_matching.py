# tests/integration/test_fuzzy_matching.py
"""Integration tests for FuzzyMatchingStrategy, exercised directly
(not through the orchestrator) so each decision path is pinned."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from recon.matching.blocking.candidates import BlockingConfig
from recon.matching.strategies.fuzzy import FuzzyMatchingStrategy
from recon.persistence.models import MatchClaim, MatchResult, NormalisedTransaction
from tests.integration.factories import make_ingestion_file, make_txn


def _strategy(session: Session, run_id: str) -> FuzzyMatchingStrategy:
    return FuzzyMatchingStrategy(
        session,
        run_id,
        BlockingConfig(amount_bucket_width_minor=10_000, date_window_days=2),
        amount_tolerance_minor=100,
    )


def _external(ingestion_file_id: uuid.UUID, **overrides: object) -> NormalisedTransaction:
    return make_txn(
        ingestion_file_id,
        source="EXTERNAL",
        id=uuid.uuid4(),
        raw_transaction_id=uuid.uuid4(),
        **overrides,
    )


def test_high_confidence_pair_is_auto_matched_and_marked_matched(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    internal = make_txn(f.id, counterparty_name_normalised="ACME CORPORATION")
    external = _external(f.id, counterparty_name_normalised="ACME CORPORATION")
    db_session.add_all([internal, external])
    db_session.flush()

    outcome = _strategy(db_session, "fz-1").run([internal], [external])

    assert outcome.auto_matched_count == 1
    result = db_session.get(MatchResult, outcome.match_result_ids[0])
    assert result is not None
    assert result.match_type == "FUZZY"
    assert result.status == "AUTO_MATCHED"
    assert result.confidence == Decimal("1.000")
    assert internal.match_status == "MATCHED"
    assert external.match_status == "MATCHED"


def test_review_band_pair_is_written_pending_review_claimed_but_not_matched(
    db_session: Session,
) -> None:
    """Same reference and amount (0.35 + 0.25) plus the two gates (0.15)
    = 0.75, with the dates far apart so date and counterparty score 0.
    Lands in the review band: AE-10 says it is written, but not counted
    as a match."""
    f = make_ingestion_file(db_session)
    internal = make_txn(f.id, txn_date=date(2026, 3, 15))
    external = _external(f.id, txn_date=date(2026, 3, 25))
    db_session.add_all([internal, external])
    db_session.flush()

    outcome = _strategy(db_session, "fz-2").run([internal], [external])

    assert outcome.review_count == 1
    assert outcome.auto_matched_count == 0
    result = db_session.get(MatchResult, outcome.match_result_ids[0])
    assert result is not None
    assert result.status == "PENDING_REVIEW"
    assert result.confidence == Decimal("0.750")

    assert internal.match_status == "UNMATCHED"  # only a human confirmation flips this
    assert external.match_status == "UNMATCHED"
    claims = db_session.query(MatchClaim).filter(MatchClaim.match_result_id == result.id).all()
    assert len(claims) == 2  # still claimed, so nothing else can grab either side


def test_below_review_threshold_writes_nothing(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    internal = make_txn(
        f.id,
        normalised_reference="TOTALLYDIFFERENT",
        amount_minor=999_999_999,
        txn_date=date(2020, 1, 1),
    )
    external = _external(f.id)
    db_session.add_all([internal, external])
    db_session.flush()

    outcome = _strategy(db_session, "fz-3").run([internal], [external])

    assert outcome.auto_matched_count == 0
    assert outcome.review_count == 0
    assert db_session.query(MatchResult).count() == 0


def test_two_externals_competing_for_one_internal_only_first_wins_and_keeps_its_match(
    db_session: Session,
) -> None:
    """Regression test for the rollback bug: the losing claim must not
    erase the winning match made earlier in the same session."""
    f = make_ingestion_file(db_session)
    internal = make_txn(f.id, counterparty_name_normalised="ACME CORPORATION")
    first = _external(f.id, counterparty_name_normalised="ACME CORPORATION")
    second = _external(f.id, counterparty_name_normalised="ACME CORPORATION")
    db_session.add_all([internal, first, second])
    db_session.flush()

    outcome = _strategy(db_session, "fz-4").run([internal], [first, second])

    assert outcome.auto_matched_count == 1
    assert outcome.skipped_claim_conflict_count == 1
    assert db_session.query(MatchResult).count() == 1  # the first match survived
    assert first.match_status == "MATCHED"
    assert second.match_status == "UNMATCHED"
    # and the loser left no orphaned claim behind
    assert (
        db_session.query(MatchClaim)
        .filter(MatchClaim.normalised_transaction_id == second.id)
        .count()
        == 0
    )
