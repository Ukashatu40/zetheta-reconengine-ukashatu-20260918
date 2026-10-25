# src/recon/fx/service.py
"""Exchange-rate lookup (A5.1).

Source hierarchy: RBI reference rate, then FBIL, then a bank's card rate. A
rate flagged suspect at load time is used only when no clean rate exists. If
nothing exists for the booking date, the most recent earlier rate within
max_stale_days is used and the quote says how stale it is, so a caller (and
the audit trail) can see that the rate was not from the day itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.persistence.models import FxRate

_SOURCE_RANK = {"RBI": 0, "FBIL": 1, "BANK_CARD": 2}
DEFAULT_MAX_STALE_DAYS = 3


@dataclass(frozen=True, slots=True)
class FxQuote:
    currency: str
    rate_inr: Decimal
    source: str
    rate_date: date
    days_stale: int
    suspect: bool


def _quote(row: FxRate, on: date) -> FxQuote:
    return FxQuote(
        currency=row.currency,
        rate_inr=row.rate_inr,
        source=row.source,
        rate_date=row.rate_date,
        days_stale=(on - row.rate_date).days,
        suspect=row.suspect,
    )


def pick_quote(rows: Iterable[FxRate], on: date, max_stale_days: int) -> FxQuote | None:
    """Best rate for `on` among rows of one currency: the latest date within the
    staleness window, then clean before suspect, then the source hierarchy."""
    eligible = [r for r in rows if 0 <= (on - r.rate_date).days <= max_stale_days]
    if not eligible:
        return None
    best = min(
        eligible, key=lambda r: (-r.rate_date.toordinal(), r.suspect, _SOURCE_RANK[r.source])
    )
    # A clean rate on an older date beats a suspect rate on the day itself.
    clean = [r for r in eligible if not r.suspect]
    if best.suspect and clean:
        best = min(clean, key=lambda r: (-r.rate_date.toordinal(), _SOURCE_RANK[r.source]))
    return _quote(best, on)


class FxService:
    def __init__(self, session: Session, *, max_stale_days: int = DEFAULT_MAX_STALE_DAYS) -> None:
        self._session = session
        self._max_stale_days = max_stale_days
        self._cache: dict[tuple[str, date], FxQuote | None] = {}

    def quote(self, currency: str, on: date) -> FxQuote | None:
        """INR per one unit of `currency` on `on`; INR itself is always 1."""
        if currency == "INR":
            return FxQuote("INR", Decimal(1), "IDENTITY", on, 0, False)
        key = (currency, on)
        if key not in self._cache:
            rows = self._session.scalars(
                select(FxRate).where(
                    FxRate.currency == currency,
                    FxRate.rate_date <= on,
                    FxRate.rate_date >= on - timedelta(days=self._max_stale_days),
                )
            ).all()
            self._cache[key] = pick_quote(rows, on, self._max_stale_days)
        return self._cache[key]
