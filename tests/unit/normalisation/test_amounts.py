# tests/unit/normalisation/test_amounts.py
"""Tests for recon.normalisation.amounts."""

from __future__ import annotations

from decimal import Decimal

import pytest

from recon.normalisation.amounts import (
    AmountNormalisationError,
    normalise_amount,
    sign_for_direction,
)


def test_plain_decimal_amount_normalises_correctly() -> None:
    money = normalise_amount("1500.00", "INR")
    assert money.amount == Decimal("1500.00")
    assert money.minor_units == 150_000


def test_comma_decimal_separator_is_configurable() -> None:
    """MT940's default comma decimal (SBI's convention)."""
    money = normalise_amount("1500,00", "INR", decimal_separator=",")
    assert money.amount == Decimal("1500.00")


def test_thousands_separator_is_stripped_before_decimal_parsing() -> None:
    money = normalise_amount("1,500.00", "INR", decimal_separator=".", thousands_separator=",")
    assert money.amount == Decimal("1500.00")


def test_period_decimal_with_no_thousands_separator() -> None:
    """Axis's MT940 configuration: period decimal, no thousands
    separator configured at all."""
    money = normalise_amount("1500.00", "INR", decimal_separator=".")
    assert money.amount == Decimal("1500.00")


def test_invalid_amount_text_raises() -> None:
    with pytest.raises(AmountNormalisationError, match="could not be parsed"):
        normalise_amount("not-a-number", "INR")


def test_unknown_currency_raises() -> None:
    with pytest.raises(AmountNormalisationError, match="no minor-unit exponent"):
        normalise_amount("100.00", "ZZZ")


def test_jpy_zero_decimal_currency() -> None:
    money = normalise_amount("1500", "JPY")
    assert money.minor_units == 1500


def test_rounding_mode_is_configurable() -> None:
    half_even = normalise_amount("2.005", "INR")  # default ROUND_HALF_EVEN
    half_up = normalise_amount("2.005", "INR", rounding="ROUND_HALF_UP")

    assert half_even.amount == Decimal("2.00")
    assert half_up.amount == Decimal("2.01")


@pytest.mark.parametrize(
    ("direction", "expected_sign"),
    [
        ("C", 1),
        ("c", 1),
        ("RC", 1),
        ("CRDT", 1),
        ("D", -1),
        ("d", -1),
        ("RD", -1),
        ("DBIT", -1),
    ],
)
def test_sign_for_direction_recognises_all_mt940_and_camt053_marks(
    direction: str, expected_sign: int
) -> None:
    assert sign_for_direction(direction) == expected_sign


def test_sign_for_direction_rejects_unrecognised_text() -> None:
    with pytest.raises(ValueError, match="not a recognised direction mark"):
        sign_for_direction("SIDEWAYS")
