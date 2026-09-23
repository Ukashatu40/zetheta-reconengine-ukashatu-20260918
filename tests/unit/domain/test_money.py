# tests/unit/domain/test_money.py
"""Tests for recon.domain.money.

Includes the AE-04 float-drift demonstration test and the per-currency
exponent tests (JPY 0dp, KWD 3dp) that prove the design decisions in
docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md are actually enforced, not just
described.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest

from recon.domain.money import (
    Money,
    UnknownCurrencyError,
    currency_exponent,
    register_currency_exponent,
    sum_money,
)


def test_float_representation_drift_demonstration() -> None:
    """AE-04: documents why float is unsafe for money, independent of
    rounding mode. This is the exact 0.1 + 0.2 case the PDF's Case Study C3
    cites — included here as a permanent, visible regression guard proving
    the codebase never relies on float arithmetic for money.
    """
    assert 0.1 + 0.2 != 0.3  # the float artefact itself, documented not fixed

    # The Decimal/minor-unit path used throughout recon.domain does not
    # exhibit this: 10 paise + 20 paise is exactly 30 paise.
    ten_paise = Money.from_minor_units(10, "INR")
    twenty_paise = Money.from_minor_units(20, "INR")
    assert (ten_paise + twenty_paise).minor_units == 30
    assert (ten_paise + twenty_paise).amount == Decimal("0.30")


def test_rounding_mode_is_configured_not_default() -> None:
    """AE-04: rounding must be an explicit parameter, never the ambient
    decimal.Context. 0.005 rounds differently under HALF_EVEN vs HALF_UP;
    both must be reachable by passing `rounding` explicitly."""
    half_even = Money.from_decimal(Decimal("2.005"), "INR")  # default HALF_EVEN
    half_up = Money.from_decimal(Decimal("2.005"), "INR", rounding=ROUND_HALF_UP)

    assert half_even.amount == Decimal("2.00")  # 2.005 -> even neighbour 2.00
    assert half_up.amount == Decimal("2.01")  # 2.005 -> rounds away from zero


def test_sum_of_10000_amounts_is_exact() -> None:
    """Cumulative-drift guard: summing many small amounts must not
    accumulate error the way repeated float addition would (Case Study C3's
    'cumulative rounding difference reached crores')."""
    amounts = [Money.from_decimal(Decimal("0.01"), "INR") for _ in range(10_000)]
    total = sum_money(amounts, "INR")
    assert total.amount == Decimal("100.00")
    assert total.minor_units == 10_000


@pytest.mark.parametrize(
    ("currency", "amount", "expected_minor"),
    [
        ("INR", Decimal("100.50"), 10_050),
        ("USD", Decimal("1.00"), 100),
        ("JPY", Decimal("1500"), 1_500),  # 0 decimal places
        ("KWD", Decimal("1.234"), 1_234),  # 3 decimal places
    ],
)
def test_per_currency_exponent(currency: str, amount: Decimal, expected_minor: int) -> None:
    money = Money.from_decimal(amount, currency)
    assert money.minor_units == expected_minor


def test_jpy_quantizes_to_zero_decimal_places() -> None:
    money = Money.from_decimal(Decimal("1500.7"), "JPY", rounding="ROUND_HALF_EVEN")
    assert money.amount == Decimal("1501")
    assert money.minor_units == 1501


def test_unknown_currency_raises_rather_than_defaulting() -> None:
    with pytest.raises(UnknownCurrencyError):
        Money.from_decimal(Decimal("10.00"), "XYZ")


def test_register_currency_exponent_extends_support() -> None:
    register_currency_exponent("XYZ", 2)
    assert currency_exponent("XYZ") == 2
    money = Money.from_decimal(Decimal("5.55"), "XYZ")
    assert money.minor_units == 555


def test_cross_currency_arithmetic_is_rejected() -> None:
    inr = Money.from_decimal(Decimal("100"), "INR")
    usd = Money.from_decimal(Decimal("100"), "USD")
    with pytest.raises(ValueError, match="cannot combine"):
        _ = inr + usd


def test_from_minor_units_round_trip_is_lossless() -> None:
    original = Money.from_decimal(Decimal("999.99"), "INR")
    reconstructed = Money.from_minor_units(original.minor_units, "INR")
    assert reconstructed == original


def test_money_construction_rejects_inconsistent_amount_and_minor_units() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        Money(amount=Decimal("10.00"), currency="INR", minor_units=999)


def test_abs_diff_is_currency_safe_and_symmetric() -> None:
    a = Money.from_decimal(Decimal("100.00"), "INR")
    b = Money.from_decimal(Decimal("97.50"), "INR")
    assert a.abs_diff(b) == b.abs_diff(a)
    assert a.abs_diff(b).amount == Decimal("2.50")
