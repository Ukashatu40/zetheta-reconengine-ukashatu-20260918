# src/recon/excmgmt/pair_analysis.py
"""Decides which exception category (if any) describes an internal/external pair.

One pure function serves every situation the classifier meets:

  * a pair of unmatched leftovers that share a reference (matched=False)
  * a pair the fuzzy matcher parked in the review band (matched=False)
  * a pair already matched, whose dates or reference still differ (matched=True)

For a pair that is NOT matched the first applicable category wins, in this order:
REGULATORY_HOLD, DIRECTION_REVERSAL, currency (FX_VARIANCE / CURRENCY_MISMATCH),
REFERENCE_TRUNCATED, amount (FEE_DEDUCTION / AMOUNT_MISMATCH), dates
(TIMEZONE_OFFSET / DATE_MISMATCH). For a pair that IS matched, amounts, currency
and direction already agree, so only the findings that survive a match are
reported: REGULATORY_HOLD, REFERENCE_TRUNCATED, TIMEZONE_OFFSET / DATE_MISMATCH
and SETTLEMENT_DELAY, and each is its own exception.

Dates are compared in business days (A3.3) using the bank's calendar. A one-day
gap whose instants are less than timezone_offset_max_gap_hours apart is a
timezone artefact (A5.2), not a late booking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from recon.calendar import BusinessCalendar
from recon.config.models import BankConfig
from recon.domain.enums import ExceptionCategory
from recon.excmgmt.taxonomy import DetectionConfig
from recon.fx.convert import relative_difference_percent, to_inr_minor
from recon.fx.service import FxService
from recon.persistence.models import NormalisedTransaction

_SECONDS_PER_HOUR = 3600
_HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class PairContext:
    detection: DetectionConfig
    calendar: BusinessCalendar
    bank: BankConfig | None = None
    fx: FxService | None = None
    fx_tolerance_percent: Decimal = Decimal("1.5")

    @property
    def allowed_business_days(self) -> int:
        return self.bank.date_offset_days if self.bank else 1

    @property
    def settlement_days(self) -> int:
        return self.bank.settlement_days if self.bank else 1


@dataclass(frozen=True, slots=True)
class PairFinding:
    category: ExceptionCategory
    why: str
    amount_difference_minor: int | None = None
    days_offset: int | None = None
    fuzzy_confidence: Decimal | None = None
    fx_variance_percent: Decimal | None = None
    details: dict[str, Any] = field(default_factory=dict)


def keyword_pattern(keywords: list[str]) -> re.Pattern[str] | None:
    """Whole-word, case-insensitive pattern for a keyword list; None if empty."""
    cleaned = [k.strip() for k in keywords if k.strip()]
    if not cleaned:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(k) for k in cleaned) + r")\b", re.IGNORECASE)


def narration_of(txn: NormalisedTransaction) -> str:
    return txn.narration_normalised or txn.narration or ""


def has_regulatory_hold(txn: NormalisedTransaction, detection: DetectionConfig) -> bool:
    pattern = keyword_pattern(detection.regulatory_hold_keywords)
    return bool(pattern and pattern.search(narration_of(txn)))


def is_posted_reversal(txn: NormalisedTransaction, detection: DetectionConfig) -> bool:
    if txn.is_reversal:
        return True
    pattern = keyword_pattern(detection.reversal_keywords)
    return bool(pattern and pattern.search(narration_of(txn)))


def is_truncated_pair(
    internal: NormalisedTransaction, external: NormalisedTransaction, detection: DetectionConfig
) -> bool:
    """The references differ, and the shorter is exactly the bank's cut length and
    is a prefix of the longer (A4.1 #15)."""
    a, b = internal.normalised_reference or "", external.normalised_reference or ""
    if not a or not b or a == b:
        return False
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return len(short) == detection.truncated_reference_length and long_.startswith(short)


def expected_fee_minor(bank: BankConfig | None, amount_minor: int) -> int | None:
    if bank is None or bank.fee_schedule is None:
        return None
    schedule = bank.fee_schedule
    percent_part = (Decimal(amount_minor) * schedule.percent / _HUNDRED).to_integral_value(
        ROUND_HALF_EVEN
    )
    return schedule.fixed_minor + int(percent_part)


def _fee_explains(
    internal: NormalisedTransaction, external: NormalisedTransaction, ctx: PairContext
) -> int | None:
    """The fee in minor units if the amount difference is the bank's fee. A bank
    takes its fee out of an incoming credit and adds it to an outgoing debit."""
    fee = expected_fee_minor(ctx.bank, internal.amount_minor)
    if fee is None or fee <= 0 or ctx.bank is None or ctx.bank.fee_schedule is None:
        return None
    gap = (
        internal.amount_minor - external.amount_minor
        if internal.direction == "CR"
        else external.amount_minor - internal.amount_minor
    )
    if gap > 0 and abs(gap - fee) <= ctx.bank.fee_schedule.tolerance_minor:
        return gap
    return None


def _instant_gap_hours(a: NormalisedTransaction, b: NormalisedTransaction) -> float:
    return abs((a.txn_timestamp_utc - b.txn_timestamp_utc).total_seconds()) / _SECONDS_PER_HOUR


def _date_finding(
    internal: NormalisedTransaction, external: NormalisedTransaction, ctx: PairContext
) -> PairFinding | None:
    if internal.txn_date == external.txn_date:
        return None
    calendar_days = abs((internal.txn_date - external.txn_date).days)
    if (
        calendar_days == 1
        and _instant_gap_hours(internal, external) < ctx.detection.timezone_offset_max_gap_hours
    ):
        return PairFinding(
            ExceptionCategory.TIMEZONE_OFFSET,
            f"dates differ by 1 day but the instants are "
            f"{_instant_gap_hours(internal, external):.1f}h apart: a timezone artefact",
            days_offset=1,
        )
    business_days = ctx.calendar.business_days_between(internal.txn_date, external.txn_date)
    # A weekend or holiday between the dates can make two different days zero
    # business days apart; the dates still differ, so report at least one.
    days = max(1, business_days)
    return PairFinding(
        ExceptionCategory.DATE_MISMATCH,
        f"dates differ by {days} business day(s) ({calendar_days} calendar)",
        days_offset=days,
    )


def _settlement_delay(
    internal: NormalisedTransaction, external: NormalisedTransaction, ctx: PairContext
) -> PairFinding | None:
    settled = external.settlement_date or external.txn_date
    late = ctx.calendar.business_days_between(internal.txn_date, settled)
    if late <= ctx.settlement_days:
        return None
    return PairFinding(
        ExceptionCategory.SETTLEMENT_DELAY,
        f"settled {late} business day(s) after the transaction; the cycle is "
        f"T+{ctx.settlement_days}",
        days_offset=late,
        details={"settlement_date": settled.isoformat(), "allowed_days": ctx.settlement_days},
    )


def _currency_finding(
    internal: NormalisedTransaction, external: NormalisedTransaction, ctx: PairContext
) -> PairFinding:
    plain = PairFinding(
        ExceptionCategory.CURRENCY_MISMATCH,
        f"currency {internal.currency} internally but {external.currency} at the bank",
    )
    if ctx.fx is None:
        return plain
    quotes = [ctx.fx.quote(t.currency, t.txn_date) for t in (internal, external)]
    if quotes[0] is None or quotes[1] is None:
        return PairFinding(
            ExceptionCategory.CURRENCY_MISMATCH, plain.why + "; no exchange rate is available"
        )
    internal_inr = to_inr_minor(internal.amount_minor, internal.currency, quotes[0].rate_inr)
    external_inr = to_inr_minor(external.amount_minor, external.currency, quotes[1].rate_inr)
    variance = relative_difference_percent(internal_inr, external_inr)
    if variance <= ctx.fx_tolerance_percent:
        return PairFinding(
            ExceptionCategory.CURRENCY_MISMATCH,
            plain.why + f"; the amounts agree after conversion (variance {variance:.4f}%)",
        )
    return PairFinding(
        ExceptionCategory.FX_VARIANCE,
        f"{internal.currency} {internal.amount_minor} vs {external.currency} "
        f"{external.amount_minor} minor units differ by {variance:.4f}% after conversion "
        f"(tolerance {ctx.fx_tolerance_percent}%)",
        fx_variance_percent=variance,
        details={"internal_inr_minor": internal_inr, "external_inr_minor": external_inr},
    )


def _hold(
    internal: NormalisedTransaction, external: NormalisedTransaction, ctx: PairContext
) -> PairFinding | None:
    held = [t for t in (internal, external) if has_regulatory_hold(t, ctx.detection)]
    if not held:
        return None
    side = "internal" if held[0] is internal else "external"
    return PairFinding(
        ExceptionCategory.REGULATORY_HOLD,
        f"the {side} record's narration marks it as held for compliance review",
    )


def analyse_pair(
    internal: NormalisedTransaction,
    external: NormalisedTransaction,
    ctx: PairContext,
    *,
    matched: bool,
    confidence: Decimal | None = None,
) -> list[PairFinding]:
    """Every finding for the pair, most important first. Unmatched pairs yield at
    most one; a matched pair can yield several."""
    hold = _hold(internal, external, ctx)
    if matched:
        return _analyse_matched(internal, external, ctx, hold, confidence)
    if hold:
        return [hold]
    return _analyse_unmatched(internal, external, ctx, confidence)


def _truncation_finding(
    internal: NormalisedTransaction,
    external: NormalisedTransaction,
    ctx: PairContext,
    confidence: Decimal | None,
) -> PairFinding:
    return PairFinding(
        ExceptionCategory.REFERENCE_TRUNCATED,
        f"reference {external.normalised_reference} is the bank's "
        f"{ctx.detection.truncated_reference_length}-character cut of "
        f"{internal.normalised_reference}",
        fuzzy_confidence=confidence,
    )


def _analyse_matched(
    internal: NormalisedTransaction,
    external: NormalisedTransaction,
    ctx: PairContext,
    hold: PairFinding | None,
    confidence: Decimal | None,
) -> list[PairFinding]:
    """Amounts, currency and direction already agree; report what a match leaves."""
    findings = [hold] if hold else []
    if is_truncated_pair(internal, external, ctx.detection):
        findings.append(_truncation_finding(internal, external, ctx, confidence))
    for finding in (
        _date_finding(internal, external, ctx),
        _settlement_delay(internal, external, ctx),
    ):
        if finding:
            findings.append(finding)
    return findings


def _analyse_unmatched(
    internal: NormalisedTransaction,
    external: NormalisedTransaction,
    ctx: PairContext,
    confidence: Decimal | None,
) -> list[PairFinding]:
    same_money = (
        internal.amount_minor == external.amount_minor and internal.currency == external.currency
    )
    if internal.direction != external.direction:
        if not same_money:
            return []  # opposite direction AND a different amount: not confidently a reversal
        return [
            PairFinding(
                ExceptionCategory.DIRECTION_REVERSAL,
                f"direction {internal.direction} internally but {external.direction} at the bank",
            )
        ]
    if internal.currency != external.currency:
        return [_currency_finding(internal, external, ctx)]
    if same_money and is_truncated_pair(internal, external, ctx.detection):
        return [_truncation_finding(internal, external, ctx, confidence)]
    if internal.amount_minor != external.amount_minor:
        return [_amount_finding(internal, external, ctx)]
    date_finding = _date_finding(internal, external, ctx)
    return [date_finding] if date_finding else []


def _amount_finding(
    internal: NormalisedTransaction, external: NormalisedTransaction, ctx: PairContext
) -> PairFinding:
    difference = abs(internal.amount_minor - external.amount_minor)
    fee = _fee_explains(internal, external, ctx)
    if fee is not None:
        return PairFinding(
            ExceptionCategory.FEE_DEDUCTION,
            f"the bank took {fee} minor units, which is its published fee",
            amount_difference_minor=difference,
            details={"fee_minor": fee},
        )
    return PairFinding(
        ExceptionCategory.AMOUNT_MISMATCH,
        f"amounts differ by {difference} minor unit(s)",
        amount_difference_minor=difference,
    )
