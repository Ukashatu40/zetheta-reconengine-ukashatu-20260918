"""Pair analysis: which exception category describes an internal/external pair."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest

from recon.calendar import default_calendar
from recon.config.models import BankConfig, FeeScheduleConfig
from recon.domain.enums import ExceptionCategory as C
from recon.excmgmt.pair_analysis import (
    PairContext,
    analyse_pair,
    expected_fee_minor,
    has_regulatory_hold,
    is_posted_reversal,
    is_truncated_pair,
)
from recon.excmgmt.taxonomy import DetectionConfig
from recon.fx.service import FxQuote
from recon.persistence.models import NormalisedTransaction

_DAY = date(2026, 3, 16)  # a Monday


def _txn(**overrides: Any) -> NormalisedTransaction:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "txn_date": _DAY,
        "currency": "INR",
        "direction": "CR",
        "amount": Decimal("1000.00"),
        "amount_minor": 100_000,
        "normalised_reference": "REF1234567890123456",
        "is_reversal": False,
        "narration_normalised": None,
        "narration": None,
        "settlement_date": None,
    }
    values.update(overrides)
    if "txn_timestamp_utc" not in values:
        values["txn_timestamp_utc"] = datetime.combine(values["txn_date"], time(12), tzinfo=UTC)
    return NormalisedTransaction(**values)


def _bank(**overrides: Any) -> BankConfig:
    values: dict[str, Any] = {
        "bank_code": "HDFC",
        "bank_name": "HDFC Bank",
        "config_version": "hdfc.v1",
        "supported_formats": ["CSV"],
        "timezone": "Asia/Kolkata",
        "currency_default": "INR",
        "settlement_cycle": "T+1",
        "reconciliation_window_days": 1,
    }
    values.update(overrides)
    return BankConfig(**values)


class _Fx:
    """A stand-in FxService with fixed INR-per-unit rates."""

    def __init__(self, rates: dict[str, str]) -> None:
        self._rates = rates

    def quote(self, currency: str, on: date) -> FxQuote | None:
        if currency == "INR":
            return FxQuote("INR", Decimal(1), "IDENTITY", on, 0, False)
        rate = self._rates.get(currency)
        return FxQuote(currency, Decimal(rate), "RBI", on, 0, False) if rate else None


def _ctx(**overrides: Any) -> PairContext:
    values: dict[str, Any] = {
        "detection": DetectionConfig(),
        "calendar": default_calendar("in"),
        "bank": _bank(),
    }
    values.update(overrides)
    return PairContext(**values)


def _one(internal: NormalisedTransaction, external: NormalisedTransaction, **kw: Any) -> Any:
    findings = analyse_pair(internal, external, kw.pop("ctx", _ctx()), matched=False, **kw)
    assert len(findings) <= 1
    return findings[0] if findings else None


# -- unmatched pairs -----------------------------------------------------------


def test_an_identical_pair_has_no_finding() -> None:
    assert _one(_txn(), _txn()) is None


def test_opposite_direction_with_the_same_money_is_a_reversal() -> None:
    finding = _one(_txn(direction="CR"), _txn(direction="DR"))
    assert finding.category is C.DIRECTION_REVERSAL


def test_opposite_direction_with_a_different_amount_is_not_called_a_reversal() -> None:
    assert _one(_txn(direction="CR"), _txn(direction="DR", amount_minor=5)) is None


def test_a_different_currency_without_fx_is_a_currency_mismatch() -> None:
    finding = _one(_txn(), _txn(currency="USD"))
    assert finding.category is C.CURRENCY_MISMATCH


def test_a_different_currency_with_a_missing_rate_says_so() -> None:
    finding = _one(_txn(), _txn(currency="USD"), ctx=_ctx(fx=_Fx({})))
    assert (finding.category, "no exchange rate" in finding.why) == (C.CURRENCY_MISMATCH, True)


def test_an_fx_gap_beyond_tolerance_is_fx_variance_with_the_measured_percentage() -> None:
    # USD 1,300 at 83.4 = INR 108,420 against INR 100,000: about 8.1%
    finding = _one(
        _txn(amount_minor=10_000_000),
        _txn(currency="USD", amount_minor=130_000),
        ctx=_ctx(fx=_Fx({"USD": "83.4"})),
    )
    assert finding.category is C.FX_VARIANCE
    assert Decimal("8.0") < finding.fx_variance_percent < Decimal("8.2")


def test_an_fx_gap_within_tolerance_is_still_a_currency_mismatch() -> None:
    finding = _one(
        _txn(amount_minor=10_000_000),
        _txn(currency="USD", amount_minor=120_000),
        ctx=_ctx(fx=_Fx({"USD": "83.4"})),
    )
    assert finding.category is C.CURRENCY_MISMATCH and "agree after conversion" in finding.why


def test_an_amount_gap_is_an_amount_mismatch_carrying_the_difference() -> None:
    finding = _one(_txn(), _txn(amount_minor=100_500))
    assert (finding.category, finding.amount_difference_minor) == (C.AMOUNT_MISMATCH, 500)


def test_a_gap_equal_to_the_published_fee_is_a_fee_deduction_on_a_credit() -> None:
    bank = _bank(fee_schedule=FeeScheduleConfig(fixed_minor=500, percent=Decimal("0.10")))
    # fee = 500 + 0.10% of 100,000 = 600; the bank paid us 100,000 - 600
    finding = _one(_txn(), _txn(amount_minor=99_400), ctx=_ctx(bank=bank))
    assert finding.category is C.FEE_DEDUCTION
    assert finding.details == {"fee_minor": 600}


def test_a_fee_on_a_debit_makes_the_bank_amount_larger() -> None:
    bank = _bank(fee_schedule=FeeScheduleConfig(fixed_minor=500))
    ctx = _ctx(bank=bank)
    ok = _one(_txn(direction="DR"), _txn(direction="DR", amount_minor=100_500), ctx=ctx)
    wrong_way = _one(_txn(direction="DR"), _txn(direction="DR", amount_minor=99_500), ctx=ctx)
    assert (ok.category, wrong_way.category) == (C.FEE_DEDUCTION, C.AMOUNT_MISMATCH)


def test_the_fee_is_matched_within_the_schedule_tolerance_only() -> None:
    bank = _bank(fee_schedule=FeeScheduleConfig(fixed_minor=500, tolerance_minor=1))
    ctx = _ctx(bank=bank)
    near = _one(_txn(), _txn(amount_minor=99_501), ctx=ctx)  # fee 499: within 1
    far = _one(_txn(), _txn(amount_minor=99_503), ctx=ctx)  # fee 497: outside
    assert (near.category, far.category) == (C.FEE_DEDUCTION, C.AMOUNT_MISMATCH)


def test_without_a_fee_schedule_no_gap_is_a_fee() -> None:
    assert _one(_txn(), _txn(amount_minor=99_500)).category is C.AMOUNT_MISMATCH


def test_expected_fee_rounds_half_even_on_the_percentage() -> None:
    bank = _bank(fee_schedule=FeeScheduleConfig(fixed_minor=0, percent=Decimal("0.25")))
    assert expected_fee_minor(bank, 10_002) == 25  # 25.005 -> 25
    assert expected_fee_minor(None, 10_000) is None
    assert expected_fee_minor(_bank(), 10_000) is None


def test_one_day_apart_with_a_close_instant_is_a_timezone_offset() -> None:
    internal = _txn(
        txn_date=date(2026, 3, 16), txn_timestamp_utc=datetime(2026, 3, 16, 17, 30, tzinfo=UTC)
    )
    external = _txn(
        txn_date=date(2026, 3, 17), txn_timestamp_utc=datetime(2026, 3, 16, 18, 0, tzinfo=UTC)
    )
    finding = _one(internal, external)
    assert (finding.category, finding.days_offset) == (C.TIMEZONE_OFFSET, 1)


def test_a_clean_next_day_booking_is_a_date_mismatch_not_a_timezone_artefact() -> None:
    finding = _one(_txn(), _txn(txn_date=_DAY + timedelta(days=1)))
    assert (finding.category, finding.days_offset) == (C.DATE_MISMATCH, 1)


def test_date_gaps_are_counted_in_business_days() -> None:
    # Friday to Monday is one business day, three calendar days
    finding = _one(_txn(txn_date=date(2026, 3, 13)), _txn(txn_date=date(2026, 3, 16)))
    assert (finding.category, finding.days_offset) == (C.DATE_MISMATCH, 1)
    assert "3 calendar" in finding.why


def test_a_gap_hidden_by_a_holiday_is_still_reported_as_at_least_one_day() -> None:
    # Sat 14 -> Sun 15 March: zero business days, but the dates differ
    finding = _one(_txn(txn_date=date(2026, 3, 14)), _txn(txn_date=date(2026, 3, 15)))
    assert finding.days_offset == 1


def test_a_truncated_reference_is_named_with_the_fuzzy_confidence() -> None:
    long_ref = "ABCDEFGHIJKLMNOPQRST"
    finding = _one(
        _txn(normalised_reference=long_ref),
        _txn(normalised_reference=long_ref[:16]),
        confidence=Decimal("0.80"),
    )
    assert (finding.category, finding.fuzzy_confidence) == (C.REFERENCE_TRUNCATED, Decimal("0.80"))


@pytest.mark.parametrize(
    ("internal_ref", "external_ref", "expected"),
    [
        ("ABCDEFGHIJKLMNOPQRST", "ABCDEFGHIJKLMNOP", True),
        ("ABCDEFGHIJKLMNOP", "ABCDEFGHIJKLMNOPQRST", True),  # either way round
        ("ABCDEFGHIJKLMNOPQRST", "ABCDEFGHIJKLMNO", False),  # 15 chars: not the bank's cut
        ("ABCDEFGHIJKLMNOPQRST", "XBCDEFGHIJKLMNOP", False),  # not a prefix
        ("SAME", "SAME", False),
        (None, "ABCDEFGHIJKLMNOP", False),
    ],
)
def test_is_truncated_pair(internal_ref: str | None, external_ref: str, expected: bool) -> None:
    internal = _txn(normalised_reference=internal_ref)
    external = _txn(normalised_reference=external_ref)
    assert is_truncated_pair(internal, external, DetectionConfig()) is expected


def test_a_truncated_reference_with_a_different_amount_is_judged_on_the_amount() -> None:
    finding = _one(
        _txn(normalised_reference="ABCDEFGHIJKLMNOPQRST"),
        _txn(normalised_reference="ABCDEFGHIJKLMNOP", amount_minor=5),
    )
    assert finding.category is C.AMOUNT_MISMATCH


def test_a_compliance_hold_outranks_everything_else() -> None:
    finding = _one(_txn(), _txn(amount_minor=5, narration_normalised="HELD FOR AML REVIEW"))
    assert finding.category is C.REGULATORY_HOLD and "external" in finding.why


@pytest.mark.parametrize(
    ("narration", "held"),
    [
        ("HELD FOR AML REVIEW", True),
        ("aml check", True),
        ("regulatory hold placed", True),
        ("PAYMENT TO CAMELOT", False),  # no whole-word match inside another word
        ("FILMING", False),
        (None, False),
    ],
)
def test_has_regulatory_hold_matches_whole_words_case_insensitively(
    narration: str | None, held: bool
) -> None:
    assert has_regulatory_hold(_txn(narration_normalised=narration), DetectionConfig()) is held


def test_the_narration_falls_back_to_the_raw_text() -> None:
    txn = _txn(narration_normalised=None, narration="STR filed, FIU follow-up")
    assert has_regulatory_hold(txn, DetectionConfig()) is True


def test_is_posted_reversal_uses_the_flag_or_the_narration() -> None:
    detection = DetectionConfig()
    assert is_posted_reversal(_txn(is_reversal=True), detection)
    assert is_posted_reversal(_txn(narration_normalised="REVERSAL OF PAYMENT 12"), detection)
    assert not is_posted_reversal(_txn(narration_normalised="PAYMENT"), detection)


# -- matched pairs ---------------------------------------------------------------


def _matched(internal: NormalisedTransaction, external: NormalisedTransaction, **kw: Any) -> Any:
    return analyse_pair(internal, external, kw.pop("ctx", _ctx()), matched=True, **kw)


def test_an_identical_matched_pair_has_no_findings() -> None:
    assert _matched(_txn(), _txn()) == []


def test_a_matched_pair_can_carry_several_findings() -> None:
    internal = _txn(normalised_reference="ABCDEFGHIJKLMNOPQRST", txn_date=date(2026, 3, 13))
    external = _txn(
        normalised_reference="ABCDEFGHIJKLMNOP",
        txn_date=date(2026, 3, 18),
        settlement_date=date(2026, 3, 18),
    )
    categories = [f.category for f in _matched(internal, external)]
    assert categories == [C.REFERENCE_TRUNCATED, C.DATE_MISMATCH, C.SETTLEMENT_DELAY]


def test_settlement_within_the_cycle_is_not_a_delay() -> None:
    internal = _txn(txn_date=date(2026, 3, 13))
    on_time = _txn(txn_date=date(2026, 3, 13), settlement_date=date(2026, 3, 16))  # T+1 business
    late = _txn(txn_date=date(2026, 3, 13), settlement_date=date(2026, 3, 18))
    assert [f.category for f in _matched(internal, on_time)] == []
    delay = [f for f in _matched(internal, late) if f.category is C.SETTLEMENT_DELAY]
    assert delay[0].days_offset == 3 and delay[0].details["allowed_days"] == 1


def test_a_longer_settlement_cycle_widens_the_allowance() -> None:
    bank = _bank(settlement_cycle="T+3")
    internal = _txn(txn_date=date(2026, 3, 13))
    external = _txn(txn_date=date(2026, 3, 13), settlement_date=date(2026, 3, 18))
    assert not [
        f
        for f in _matched(internal, external, ctx=_ctx(bank=bank))
        if f.category is C.SETTLEMENT_DELAY
    ]


def test_a_hold_is_reported_on_a_matched_pair_too() -> None:
    findings = _matched(_txn(narration_normalised="AML HOLD"), _txn())
    assert [f.category for f in findings] == [C.REGULATORY_HOLD]


def test_without_a_bank_config_the_defaults_are_t_plus_1() -> None:
    ctx = _ctx(bank=None)
    assert (ctx.allowed_business_days, ctx.settlement_days) == (1, 1)
