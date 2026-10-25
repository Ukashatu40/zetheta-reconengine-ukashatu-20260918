# src/recon/matching/rules/date_offset.py
"""Date Offset Rule (A3.3): same reference, amount, currency and direction,
dates differing by 1..N BUSINESS days, N configured per bank.

Business days, not calendar days: a Friday booking settled on Monday is one
business day apart, and a settlement over a bank holiday is not a mismatch. A
pair on the same date is left to the exact matcher; a gap of more than N
business days is a genuine DATE_MISMATCH and is declined.
"""

from __future__ import annotations

from recon.calendar import BusinessCalendar
from recon.matching.rules.base import RuleResult
from recon.persistence.models import NormalisedTransaction


class DateOffsetRule:
    name: str = "DATE_OFFSET"
    priority: int = 20

    def __init__(
        self, calendar: BusinessCalendar, *, max_business_days: int, confidence: float = 0.90
    ) -> None:
        if max_business_days < 0:
            raise ValueError("max_business_days must be non-negative")
        if not 0 < confidence <= 1:
            raise ValueError("confidence must be in (0, 1]")
        self._calendar = calendar
        self._max_business_days = max_business_days
        self._confidence = confidence

    def evaluate(
        self, internal_txn: NormalisedTransaction, external_txn: NormalisedTransaction
    ) -> RuleResult:
        if (internal_txn.currency, internal_txn.direction) != (
            external_txn.currency,
            external_txn.direction,
        ):
            return self._decline("currency or direction differs")
        reference = internal_txn.normalised_reference
        if not reference or reference != external_txn.normalised_reference:
            return self._decline("normalised references are missing or not identical")
        if internal_txn.amount_minor != external_txn.amount_minor:
            return self._decline("amounts differ; the date rule matches equal amounts only")

        gap = self._calendar.business_days_between(internal_txn.txn_date, external_txn.txn_date)
        if gap == 0:
            return self._decline("same business day; not a date-offset case")
        if gap > self._max_business_days:
            return self._decline(f"{gap} business days apart (max {self._max_business_days})")
        return RuleResult(
            matched=True,
            confidence_contribution=self._confidence,
            explanation=(
                f"{self.name}: identical reference and amount, {gap} business day(s) apart "
                f"(calendar {self._calendar.name}, max {self._max_business_days})."
            ),
        )

    @staticmethod
    def _decline(reason: str) -> RuleResult:
        return RuleResult(matched=False, confidence_contribution=0.0, explanation=reason)
