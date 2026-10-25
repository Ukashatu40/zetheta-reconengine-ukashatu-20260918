"""Repository for fetching unmatched normalised transactions, scoped by
bank and source: the candidate pools every matching level works on."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date

from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.orm import Session

from recon.persistence.models import MatchClaim, MatchResult, NormalisedTransaction

_CHUNK = 5000


class MatchingRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def _available(
        bank_code: str, source: str, date_from: date | None, date_to: date | None
    ) -> list[ColumnElement[bool]]:
        """UNMATCHED and holding no ACTIVE claim (IB-07), optionally limited
        to a txn_date range (inclusive on both ends)."""
        actively_claimed = exists().where(
            MatchClaim.normalised_transaction_id == NormalisedTransaction.id,
            MatchClaim.status == "ACTIVE",
        )
        conditions = [
            NormalisedTransaction.bank_code == bank_code,
            NormalisedTransaction.source == source,
            NormalisedTransaction.match_status == "UNMATCHED",
            ~actively_claimed,
        ]
        if date_from is not None:
            conditions.append(NormalisedTransaction.txn_date >= date_from)
        if date_to is not None:
            conditions.append(NormalisedTransaction.txn_date <= date_to)
        return conditions

    def find_unmatched(
        self,
        bank_code: str,
        source: str,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[NormalisedTransaction]:
        """Ordered by (txn_date, source line, id) so tie-breaks are deterministic (IB-08).
        The source line comes before the random id so that of two identical rows in a file the
        FIRST is the one matched, which is also the one duplicate detection keeps as the
        original (it voids the later copy, never the matched one)."""
        return (
            self._session.query(NormalisedTransaction)
            .filter(*self._available(bank_code, source, date_from, date_to))
            .order_by(
                NormalisedTransaction.txn_date,
                NormalisedTransaction.source_line_no,
                NormalisedTransaction.id,
            )
            .all()
        )

    def count_unmatched(
        self,
        bank_code: str,
        source: str,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> int:
        return (
            self._session.scalar(
                select(func.count())
                .select_from(NormalisedTransaction)
                .where(*self._available(bank_code, source, date_from, date_to))
            )
            or 0
        )

    def rejected_pairs(self, internal_ids: Iterable[uuid.UUID]) -> set[tuple[uuid.UUID, uuid.UUID]]:
        """(internal, external) pairs a person rejected or unmatched. No matching level may
        propose or make them again."""
        ids = list(internal_ids)
        pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for start in range(0, len(ids), _CHUNK):
            rows = self._session.execute(
                select(
                    MatchResult.internal_transaction_id, MatchResult.external_transaction_id
                ).where(
                    MatchResult.status == "REJECTED",
                    MatchResult.internal_transaction_id.in_(ids[start : start + _CHUNK]),
                )
            )
            pairs.update((row[0], row[1]) for row in rows)
        return pairs
