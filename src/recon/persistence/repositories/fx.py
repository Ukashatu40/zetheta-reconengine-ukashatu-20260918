"""Repository for fx_rates: bulk insert that never overwrites an existing rate."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from recon.fx.loader import ParsedFxRate
from recon.persistence.models import FxRate

_CHUNK = 2000


class FxRateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def insert_many(self, rates: Sequence[ParsedFxRate]) -> tuple[int, int]:
        """Returns (inserted, skipped). A (date, currency, source) that already
        has a rate is skipped, never replaced: a rate that was used in a
        match must not change underneath it."""
        inserted = 0
        for start in range(0, len(rates), _CHUNK):
            chunk = rates[start : start + _CHUNK]
            statement = (
                insert(FxRate)
                .values(
                    [
                        {
                            "rate_date": r.rate_date,
                            "currency": r.currency,
                            "rate_inr": r.rate_inr,
                            "source": r.source,
                            "suspect": r.suspect,
                            "suspect_reason": r.suspect_reason,
                        }
                        for r in chunk
                    ]
                )
                .on_conflict_do_nothing(index_elements=["rate_date", "currency", "source"])
                .returning(FxRate.id)
            )
            inserted += len(self._session.execute(statement).all())
        return inserted, len(rates) - inserted
