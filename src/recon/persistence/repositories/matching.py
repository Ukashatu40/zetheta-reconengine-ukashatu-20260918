# src/recon/persistence/repositories/matching.py
"""Repository for fetching unmatched normalised transactions, scoped by
bank and source — the candidate pools ExactMatchingStrategy (and future
matching levels) operate on."""

from __future__ import annotations

from sqlalchemy.orm import Session

from recon.persistence.models import NormalisedTransaction


class MatchingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_unmatched(self, bank_code: str, source: str) -> list[NormalisedTransaction]:
        return (
            self._session.query(NormalisedTransaction)
            .filter(
                NormalisedTransaction.bank_code == bank_code,
                NormalisedTransaction.source == source,
                NormalisedTransaction.match_status == "UNMATCHED",
            )
            .all()
        )
