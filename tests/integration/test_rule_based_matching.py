# tests/integration/test_rule_based_matching.py
"""Integration tests for RuleBasedMatchingStrategy against real rows."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from recon.config.matching_loader import load_matching_config
from recon.matching.blocking.candidates import BlockingConfig
from recon.matching.rules.amount_tolerance import AmountToleranceRule
from recon.matching.rules.base import RuleRegistry
from recon.matching.strategies.rule_based import RuleBasedMatchingStrategy
from recon.persistence.models import MatchClaim, MatchResult, NormalisedTransaction
from tests.integration.factories import make_ingestion_file, make_txn

_WEIGHTS = Path(__file__).resolve().parents[2] / "config" / "matching" / "weights.yaml"


def _strategy(
    session: Session, run_id: str, rule: AmountToleranceRule | None = None, cap: int = 50
) -> RuleBasedMatchingStrategy:
    registry = RuleRegistry()
    registry.register(rule or AmountToleranceRule())
    return RuleBasedMatchingStrategy(
        session,
        run_id,
        registry,
        BlockingConfig(
            amount_bucket_width_minor=10_000,
            date_window_days=2,
            max_candidates_per_transaction=cap,
        ),
        load_matching_config(_WEIGHTS),
    )


def _external(ingestion_file_id: uuid.UUID, **overrides: object) -> NormalisedTransaction:
    return make_txn(
        ingestion_file_id,
        source="EXTERNAL",
        id=uuid.uuid4(),
        raw_transaction_id=uuid.uuid4(),
        **overrides,
    )


def _internal(ingestion_file_id: uuid.UUID, **overrides: object) -> NormalisedTransaction:
    return make_txn(
        ingestion_file_id, id=uuid.uuid4(), raw_transaction_id=uuid.uuid4(), **overrides
    )


def test_within_tolerance_pair_is_auto_matched_with_rule_id(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    internal = _internal(f.id)
    external = _external(f.id, amount_minor=150_001)
    db_session.add_all([internal, external])
    db_session.flush()

    outcome = _strategy(db_session, "rb-1").run([internal], [external])

    assert outcome.auto_matched_count == 1
    result = db_session.get(MatchResult, outcome.match_result_ids[0])
    assert result is not None
    assert result.match_type == "RULE"
    assert result.rule_id == "AMOUNT_TOLERANCE"
    assert result.status == "AUTO_MATCHED"
    assert result.confidence == Decimal("0.900")
    assert internal.match_status == "MATCHED"
    assert external.match_status == "MATCHED"


def test_outside_tolerance_writes_nothing(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    internal = _internal(f.id)
    external = _external(f.id, amount_minor=150_050)
    db_session.add_all([internal, external])
    db_session.flush()

    outcome = _strategy(db_session, "rb-2").run([internal], [external])

    assert outcome.auto_matched_count == 0
    assert db_session.query(MatchResult).count() == 0


def test_two_matching_internals_is_ambiguous_and_claims_nothing(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    a, b = _internal(f.id), _internal(f.id)
    external = _external(f.id)
    db_session.add_all([a, b, external])
    db_session.flush()

    outcome = _strategy(db_session, "rb-3").run([a, b], [external])

    assert outcome.ambiguous_count == 1
    assert outcome.auto_matched_count == 0
    assert db_session.query(MatchClaim).count() == 0


def test_truncated_candidate_list_is_treated_as_ambiguous(db_session: Session) -> None:
    """One rule hit, but the cap hid other candidates: unsafe to auto-match."""
    f = make_ingestion_file(db_session)
    hit = _internal(f.id)
    other_1 = _internal(f.id, normalised_reference="OTHER1")
    other_2 = _internal(f.id, normalised_reference="OTHER2")
    external = _external(f.id)
    db_session.add_all([hit, other_1, other_2, external])
    db_session.flush()

    outcome = _strategy(db_session, "rb-4", cap=2).run([hit, other_1, other_2], [external])

    assert outcome.ambiguous_count == 1
    assert outcome.auto_matched_count == 0


def test_consumed_internal_is_not_offered_to_a_later_external(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    internal = _internal(f.id)
    first, second = _external(f.id), _external(f.id)
    db_session.add_all([internal, first, second])
    db_session.flush()

    outcome = _strategy(db_session, "rb-5").run([internal], [first, second])

    assert outcome.auto_matched_count == 1
    assert outcome.skipped_claim_conflict_count == 0
    assert second.match_status == "UNMATCHED"


def test_rule_confidence_at_or_below_threshold_is_pending_review(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    internal = _internal(f.id)
    external = _external(f.id)
    db_session.add_all([internal, external])
    db_session.flush()

    outcome = _strategy(db_session, "rb-6", AmountToleranceRule(confidence=0.80)).run(
        [internal], [external]
    )

    assert outcome.review_count == 1
    assert outcome.auto_matched_count == 0
    result = db_session.get(MatchResult, outcome.match_result_ids[0])
    assert result is not None
    assert result.status == "PENDING_REVIEW"
    assert internal.match_status == "UNMATCHED"
    assert db_session.query(MatchClaim).filter(MatchClaim.match_result_id == result.id).count() == 2
