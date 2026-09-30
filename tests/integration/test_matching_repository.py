# tests/integration/test_matching_repository.py
"""Integration tests for MatchingRepository.find_unmatched."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from recon.matching.claims import ClaimsService
from recon.persistence.repositories.matching import MatchingRepository
from tests.integration.factories import make_ingestion_file, make_txn


def test_actively_claimed_transaction_is_not_in_the_unmatched_pool(db_session: Session) -> None:
    """IB-07: a transaction held by an active claim (for example a
    PENDING_REVIEW pair) must not be offered to a matching level again."""
    f = make_ingestion_file(db_session)
    held = make_txn(f.id, id=uuid.uuid4(), raw_transaction_id=uuid.uuid4())
    free = make_txn(f.id, id=uuid.uuid4(), raw_transaction_id=uuid.uuid4())
    db_session.add_all([held, free])
    db_session.flush()
    ClaimsService(db_session).claim(held.id, "INTERNAL")

    pool = MatchingRepository(db_session).find_unmatched("HDFC", "INTERNAL")

    assert [t.id for t in pool] == [free.id]


def test_released_claim_returns_the_transaction_to_the_pool(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    txn = make_txn(f.id)
    db_session.add(txn)
    db_session.flush()
    claims = ClaimsService(db_session)
    claims.release(claims.claim(txn.id, "INTERNAL"))

    pool = MatchingRepository(db_session).find_unmatched("HDFC", "INTERNAL")

    assert [t.id for t in pool] == [txn.id]
