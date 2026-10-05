"""Repository for fetching unmatched normalised transactions, scoped by
bank and source: the candidate pools every matching level works on."""

from __future__ import annotations

from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.orm import Session

from recon.persistence.models import MatchClaim, NormalisedTransaction


class MatchingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _available(bank_code: str, source: str) -> list[ColumnElement[bool]]:
        """UNMATCHED and holding no ACTIVE claim (IB-07)."""
        actively_claimed = exists().where(
            MatchClaim.normalised_transaction_id == NormalisedTransaction.id,
            MatchClaim.status == "ACTIVE",
        )
        return [
            NormalisedTransaction.bank_code == bank_code,
            NormalisedTransaction.source == source,
            NormalisedTransaction.match_status == "UNMATCHED",
            ~actively_claimed,
        ]

    def find_unmatched(self, bank_code: str, source: str) -> list[NormalisedTransaction]:
        """Ordered by (txn_date, id) so tie-breaks are deterministic (IB-08)."""
        return (
            self._session.query(NormalisedTransaction)
            .filter(*self._available(bank_code, source))
            .order_by(NormalisedTransaction.txn_date, NormalisedTransaction.id)
            .all()
        )

    def count_unmatched(self, bank_code: str, source: str) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(NormalisedTransaction)
                .where(*self._available(bank_code, source))
            )
            or 0
        )
