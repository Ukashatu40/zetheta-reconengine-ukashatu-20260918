# src/recon/matching/rules/amount_tolerance.py
"""Amount Tolerance Rule (A3.3, R53), same-currency only.

Cross-currency tolerance (abs difference / mean amount <= 1.5%) needs FX
conversion and is a separate rule: comparing raw INR and GBP amounts is
meaningless. This rule declines any pair whose currencies differ.

Amount agreement alone is never enough to match. The rule also requires
identical non-empty normalised references, the same direction, and dates
no more than max_days_apart calendar days apart (default 3, A3.4's
"T+3 max"). Calendar days, not business days: the business-day calendar
arrives with the Date Offset rule.

tolerance_minor is in minor units, so 1 means 0.01 for a 2-decimal
currency but a full unit for JPY. Set per-currency overrides explicitly
rather than trusting the default for zero- or three-decimal currencies.
"""

from __future__ import annotations

from collections.abc import Mapping

from recon.matching.rules.base import RuleResult
from recon.persistence.models import NormalisedTransaction


class AmountToleranceRule:
    name: str = "AMOUNT_TOLERANCE"
    priority: int = 10

    def __init__(
        self,
        *,
        default_tolerance_minor: int = 1,
        tolerance_minor_by_currency: Mapping[str, int] | None = None,
        max_days_apart: int = 3,
        confidence: float = 0.90,
    ) -> None:
        overrides = dict(tolerance_minor_by_currency or {})
        if default_tolerance_minor < 0 or any(v < 0 for v in overrides.values()):
            raise ValueError("tolerances must be non-negative")
        if max_days_apart < 0:
            raise ValueError("max_days_apart must be non-negative")
        if not 0 < confidence <= 1:
            raise ValueError("confidence must be in (0, 1]")
        self._default_tolerance_minor = default_tolerance_minor
        self._tolerance_minor_by_currency = overrides
        self._max_days_apart = max_days_apart
        self._confidence = confidence

    def evaluate(
        self, internal_txn: NormalisedTransaction, external_txn: NormalisedTransaction
    ) -> RuleResult:
        if internal_txn.currency != external_txn.currency:
            return self._decline("currencies differ; cross-currency tolerance needs FX conversion")
        if internal_txn.direction != external_txn.direction:
            return self._decline("directions differ")

        internal_ref = internal_txn.normalised_reference
        external_ref = external_txn.normalised_reference
        if not internal_ref or not external_ref or internal_ref != external_ref:
            return self._decline("normalised references are missing or not identical")

        days_apart = abs((internal_txn.txn_date - external_txn.txn_date).days)
        if days_apart > self._max_days_apart:
            return self._decline(f"dates are {days_apart} days apart (max {self._max_days_apart})")

        difference = abs(internal_txn.amount_minor - external_txn.amount_minor)
        tolerance = self._tolerance_minor_by_currency.get(
            internal_txn.currency, self._default_tolerance_minor
        )
        if difference > tolerance:
            return self._decline(f"amount difference {difference} exceeds tolerance {tolerance}")

        return RuleResult(
            matched=True,
            confidence_contribution=self._confidence,
            explanation=(
                f"{self.name}: identical reference, same currency and direction, "
                f"{days_apart} day(s) apart, amount difference {difference} minor unit(s) "
                f"within tolerance {tolerance}."
            ),
        )

    @staticmethod
    def _decline(reason: str) -> RuleResult:
        return RuleResult(matched=False, confidence_contribution=0.0, explanation=reason)
