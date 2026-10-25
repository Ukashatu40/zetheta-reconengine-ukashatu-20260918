"""Conversion on minor units, relative difference and implied rate. The B4.2
figures are the ones AE-32 corrects."""

from __future__ import annotations

from decimal import Decimal

import pytest

from recon.fx.convert import (
    from_inr_minor,
    implied_rate,
    relative_difference_percent,
    to_inr_minor,
)


def test_inr_is_never_converted() -> None:
    assert to_inr_minor(12345, "INR", Decimal("99")) == 12345
    assert from_inr_minor(12345, "INR", Decimal("99")) == 12345


def test_gbp_to_inr_scales_by_the_exponents_and_rounds_half_even() -> None:
    # GBP 142,857.14 at 84.00 is INR 11,999,999.76 (AE-32), in paise:
    assert to_inr_minor(14_285_714, "GBP", Decimal("84.00")) == 1_199_999_976
    # an exact half rounds to the even paisa: 0.5 paise -> 0, 1.5 -> 2
    assert to_inr_minor(1, "USD", Decimal("0.5")) == 0
    assert to_inr_minor(3, "USD", Decimal("0.5")) == 2


def test_a_zero_decimal_currency_scales_the_other_way() -> None:
    # JPY 1000 (exponent 0) at 0.55 INR is INR 550.00 = 55,000 paise
    assert to_inr_minor(1000, "JPY", Decimal("0.55")) == 55_000
    assert from_inr_minor(55_000, "JPY", Decimal("0.55")) == 1000


def test_a_three_decimal_currency_converts_with_its_own_exponent() -> None:
    # KWD 1.000 = 1000 fils at 270 INR = INR 270.00 = 27,000 paise
    assert to_inr_minor(1000, "KWD", Decimal("270")) == 27_000
    assert from_inr_minor(27_000, "KWD", Decimal("270")) == 1000


def test_round_trip_is_stable_for_exact_values() -> None:
    inr = to_inr_minor(10_000, "USD", Decimal("83.5"))
    assert from_inr_minor(inr, "USD", Decimal("83.5")) == 10_000


def test_relative_difference_uses_the_mean_as_the_denominator() -> None:
    # |a-b|/mean(a,b): 100 vs 101.5 -> 1.5 / 100.75
    assert relative_difference_percent(10_000, 10_150) == (Decimal(150) / Decimal("10075") * 100)
    assert relative_difference_percent(5, 5) == 0
    assert relative_difference_percent(0, 0) == 0


def test_the_b42_variance_is_about_063_percent() -> None:
    internal_inr = 1_199_999_976  # the UK partner's 84.00 netting
    reference_inr = to_inr_minor(14_285_714, "GBP", Decimal("83.47"))
    variance = relative_difference_percent(internal_inr, reference_inr)
    assert Decimal("0.62") < variance < Decimal("0.64")


def test_implied_rate_recovers_the_quoted_rate() -> None:
    rate = implied_rate(1_199_999_976, 14_285_714, "GBP")
    assert rate is not None
    assert abs(rate - Decimal("84")) < Decimal("0.000001")
    assert implied_rate(100, 0, "GBP") is None


@pytest.mark.parametrize("currency", ["USD", "JPY", "KWD"])
def test_conversions_are_integers(currency: str) -> None:
    assert isinstance(to_inr_minor(12345, currency, Decimal("83.4725")), int)
    assert isinstance(from_inr_minor(1_234_567, currency, Decimal("83.4725")), int)
