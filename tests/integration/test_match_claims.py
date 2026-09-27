# tests/integration/test_match_claims.py
"""Integration tests for ClaimsService and the match_claims exclusivity
guarantee.

test_second_claim_on_same_transaction_is_rejected is the load-bearing
test in this file: it's the direct proof that A3.1's "prevent one
internal transaction from being consumed twice" is enforced by the
database, not merely assumed from application logic.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recon.matching.claims import ClaimConflictError, ClaimsService
from recon.persistence.models import MatchClaim


def test_claim_succeeds_for_an_unclaimed_transaction(db_session: Session) -> None:
    service = ClaimsService(db_session)
    txn_id = uuid.uuid4()

    claim = service.claim(txn_id, "INTERNAL")

    assert claim.normalised_transaction_id == txn_id
    assert claim.status == "ACTIVE"
    assert claim.match_result_id is None


def test_second_claim_on_same_transaction_is_rejected(db_session: Session) -> None:
    """The core exclusivity guarantee: two ACTIVE claims on the same
    normalised_transaction_id cannot coexist, enforced by the unique
    partial index — not by this service's own logic."""
    service = ClaimsService(db_session)
    txn_id = uuid.uuid4()

    service.claim(txn_id, "INTERNAL")

    with pytest.raises(ClaimConflictError):
        service.claim(txn_id, "INTERNAL")


def test_claim_on_a_released_transaction_succeeds(db_session: Session) -> None:
    """Releasing a claim genuinely frees the transaction for a new
    claim — the exclusivity index only blocks ACTIVE claims, by design."""
    service = ClaimsService(db_session)
    txn_id = uuid.uuid4()

    first_claim = service.claim(txn_id, "INTERNAL")
    service.release(first_claim)

    second_claim = service.claim(txn_id, "INTERNAL")
    assert second_claim.status == "ACTIVE"
    assert second_claim.id != first_claim.id


def test_finalise_records_match_result_id(db_session: Session) -> None:
    service = ClaimsService(db_session)
    txn_id = uuid.uuid4()
    match_result_id = uuid.uuid4()

    claim = service.claim(txn_id, "EXTERNAL")
    service.finalise(claim, match_result_id)

    assert claim.match_result_id == match_result_id
    assert claim.status == "ACTIVE"  # finalising does not change status


def test_release_sets_status_and_timestamp(db_session: Session) -> None:
    service = ClaimsService(db_session)
    claim = service.claim(uuid.uuid4(), "INTERNAL")

    assert claim.released_at is None

    service.release(claim)

    assert claim.status == "RELEASED"
    assert claim.released_at is not None


def test_is_claimed_reflects_active_status_only(db_session: Session) -> None:
    service = ClaimsService(db_session)
    txn_id = uuid.uuid4()

    assert service.is_claimed(txn_id) is False

    claim = service.claim(txn_id, "INTERNAL")
    assert service.is_claimed(txn_id) is True

    service.release(claim)
    assert service.is_claimed(txn_id) is False


def test_invalid_role_is_rejected_by_check_constraint(db_session: Session) -> None:
    bad_claim = MatchClaim(normalised_transaction_id=uuid.uuid4(), role="SIDEWAYS", status="ACTIVE")
    db_session.add(bad_claim)

    with pytest.raises(IntegrityError, match=r"role_valid|check"):
        db_session.flush()


def test_two_different_transactions_can_both_be_claimed(db_session: Session) -> None:
    """Sanity check that the exclusivity index is scoped per-transaction,
    not a blanket single-claim-in-the-whole-table restriction."""
    service = ClaimsService(db_session)

    claim_a = service.claim(uuid.uuid4(), "INTERNAL")
    claim_b = service.claim(uuid.uuid4(), "EXTERNAL")

    assert claim_a.id != claim_b.id
    assert service.is_claimed(claim_a.normalised_transaction_id)
    assert service.is_claimed(claim_b.normalised_transaction_id)
