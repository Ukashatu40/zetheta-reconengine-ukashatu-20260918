# tests/integration/test_match_review.py
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.audit.chains import MATCHING_CHAIN
from recon.audit.logger import AuditLogger
from recon.audit.verifier import verify_chain
from recon.config.matching_loader import load_matching_config
from recon.matching.blocking.candidates import BlockingConfig
from recon.matching.claims import ClaimsService
from recon.matching.review import (
    MatchNotFoundError,
    MatchNotPendingError,
    MatchReviewService,
    ReviewStateError,
)
from recon.matching.strategies.fuzzy import FuzzyMatchingStrategy
from recon.paths import config_dir
from recon.persistence.models import AuditLog, MatchClaim, MatchResult
from recon.persistence.repositories.matching import MatchingRepository
from tests.integration.factories import make_ingestion_file, make_pending_review_pair, make_txn

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _service(session: Session) -> MatchReviewService:
    return MatchReviewService(session, AuditLogger(session))


def _claims(session: Session, match_id: uuid.UUID) -> list[MatchClaim]:
    return list(session.scalars(select(MatchClaim).where(MatchClaim.match_result_id == match_id)))


def test_confirm_matches_both_transactions_and_keeps_the_claims(db_session: Session) -> None:
    internal, external, result = make_pending_review_pair(db_session)

    _service(db_session).confirm(
        result.id, reviewer="alice", now=_NOW, note="checked the statement"
    )

    assert (result.status, result.reviewed_by, result.reviewed_at) == ("CONFIRMED", "alice", _NOW)
    assert (internal.match_status, external.match_status) == ("MATCHED", "MATCHED")
    assert {c.status for c in _claims(db_session, result.id)} == {"ACTIVE"}


def test_reject_releases_both_claims_and_returns_the_pair_to_the_pool(db_session: Session) -> None:
    internal, external, result = make_pending_review_pair(db_session)

    _service(db_session).reject(result.id, reviewer="alice", now=_NOW, reason="different payments")

    assert (result.status, result.reviewed_by) == ("REJECTED", "alice")
    claims = _claims(db_session, result.id)
    assert {c.status for c in claims} == {"RELEASED"}
    assert all(c.released_at is not None for c in claims)
    assert (internal.match_status, external.match_status) == ("UNMATCHED", "UNMATCHED")
    repository = MatchingRepository(db_session)
    assert [t.id for t in repository.find_unmatched("HDFC", "INTERNAL")] == [internal.id]
    assert [t.id for t in repository.find_unmatched("HDFC", "EXTERNAL")] == [external.id]


def test_both_actions_are_audited_on_a_verifiable_chain(db_session: Session) -> None:
    _, _, first = make_pending_review_pair(db_session)
    _, _, second = make_pending_review_pair(db_session)
    service = _service(db_session)

    service.confirm(first.id, reviewer="alice", now=_NOW)
    service.reject(second.id, reviewer="bob", now=_NOW, reason="wrong customer")

    entries = db_session.scalars(
        select(AuditLog).where(AuditLog.chain_id == MATCHING_CHAIN).order_by(AuditLog.sequence_no)
    ).all()
    assert [(e.action_type, e.actor_id) for e in entries] == [
        ("MATCH_CONFIRM", "api-key:alice"),
        ("MATCH_REJECT", "api-key:bob"),
    ]
    assert entries[1].rationale == "wrong customer"
    assert verify_chain(db_session, MATCHING_CHAIN).ok is True


def test_a_decided_match_cannot_be_decided_again(db_session: Session) -> None:
    _, _, confirmed = make_pending_review_pair(db_session)
    _, _, rejected = make_pending_review_pair(db_session)
    service = _service(db_session)
    service.confirm(confirmed.id, reviewer="a", now=_NOW)
    service.reject(rejected.id, reviewer="a", now=_NOW, reason="r")

    with pytest.raises(MatchNotPendingError, match="CONFIRMED"):
        service.reject(confirmed.id, reviewer="b", now=_NOW, reason="r")
    with pytest.raises(MatchNotPendingError, match="REJECTED"):
        service.confirm(rejected.id, reviewer="b", now=_NOW)


def test_an_automatic_match_is_not_reviewable(db_session: Session) -> None:
    _, _, result = make_pending_review_pair(db_session)
    result.status = "AUTO_MATCHED"
    db_session.flush()
    with pytest.raises(MatchNotPendingError):
        _service(db_session).confirm(result.id, reviewer="a", now=_NOW)


def test_an_unknown_match_is_not_found(db_session: Session) -> None:
    with pytest.raises(MatchNotFoundError):
        _service(db_session).confirm(uuid.uuid4(), reviewer="a", now=_NOW)


def test_confirm_refuses_and_changes_nothing_when_a_claim_is_missing(db_session: Session) -> None:
    internal, external, result = make_pending_review_pair(db_session)
    ClaimsService(db_session).release(_claims(db_session, result.id)[0])

    with pytest.raises(ReviewStateError, match="claims"):
        _service(db_session).confirm(result.id, reviewer="a", now=_NOW)

    assert result.status == "PENDING_REVIEW"
    assert (internal.match_status, external.match_status) == ("UNMATCHED", "UNMATCHED")


def test_confirm_refuses_when_a_transaction_was_matched_elsewhere(db_session: Session) -> None:
    internal, _, result = make_pending_review_pair(db_session)
    internal.match_status = "MATCHED"
    db_session.flush()

    with pytest.raises(ReviewStateError, match="no longer unmatched"):
        _service(db_session).confirm(result.id, reviewer="a", now=_NOW)
    assert result.status == "PENDING_REVIEW"


# -- a rejected pair is not proposed again ---------------------------------------


def _fuzzy(session: Session, run_id: str) -> FuzzyMatchingStrategy:
    return FuzzyMatchingStrategy(
        session,
        run_id,
        BlockingConfig(amount_bucket_width_minor=10_000, date_window_days=2),
        amount_tolerance_minor=100,
        matching_config=load_matching_config(config_dir() / "matching" / "weights.yaml"),
    )


def _digit_variant_pair(session: Session) -> tuple[object, object]:
    """References differing in one digit and no counterparty: AE-37 sends this to review."""
    f = make_ingestion_file(session)
    internal = make_txn(f.id, source="INTERNAL", normalised_reference="REF0001234567")
    external = make_txn(
        f.id,
        source="EXTERNAL",
        id=uuid.uuid4(),
        raw_transaction_id=uuid.uuid4(),
        normalised_reference="REF0001234568",
    )
    session.add_all([internal, external])
    session.flush()
    return internal, external


def test_a_rejected_pair_is_not_proposed_again(db_session: Session) -> None:
    _digit_variant_pair(db_session)
    repository = MatchingRepository(db_session)
    first = _fuzzy(db_session, "run-1").run(
        repository.find_unmatched("HDFC", "INTERNAL"), repository.find_unmatched("HDFC", "EXTERNAL")
    )
    assert first.review_count == 1
    _service(db_session).reject(
        first.match_result_ids[0], reviewer="a", now=_NOW, reason="different payments"
    )

    second = _fuzzy(db_session, "run-2").run(
        repository.find_unmatched("HDFC", "INTERNAL"), repository.find_unmatched("HDFC", "EXTERNAL")
    )

    assert (second.review_count, second.auto_matched_count) == (0, 0)
    assert db_session.query(MatchResult).count() == 1


def test_rejecting_one_pair_does_not_block_the_internal_from_a_different_external(
    db_session: Session,
) -> None:
    internal, rejected_external = _digit_variant_pair(db_session)
    other = make_txn(
        make_ingestion_file(db_session).id,
        source="EXTERNAL",
        id=uuid.uuid4(),
        raw_transaction_id=uuid.uuid4(),
        normalised_reference="REF0001234568",
    )
    db_session.add(other)
    db_session.flush()
    repository = MatchingRepository(db_session)

    first = _fuzzy(db_session, "run-1").run([internal], [rejected_external])  # type: ignore[list-item]
    _service(db_session).reject(first.match_result_ids[0], reviewer="a", now=_NOW, reason="r")
    second = _fuzzy(db_session, "run-2").run(
        repository.find_unmatched("HDFC", "INTERNAL"), repository.find_unmatched("HDFC", "EXTERNAL")
    )

    assert second.review_count == 1
    proposed = db_session.get(MatchResult, second.match_result_ids[0])
    assert proposed is not None and proposed.external_transaction_id == other.id
