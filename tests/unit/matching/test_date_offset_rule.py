"""Date Offset Rule: business days, per-bank window, equal amounts only."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from recon.calendar import default_calendar
from recon.matching.rules.date_offset import DateOffsetRule
from recon.persistence.models import NormalisedTransaction


def _txn(day: date, **overrides: Any) -> NormalisedTransaction:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "txn_date": day,
        "currency": "INR",
        "direction": "CR",
        "amount": Decimal("100.00"),
        "amount_minor": 10_000,
        "normalised_reference": "REF123",
    }
    values.update(overrides)
    return NormalisedTransaction(**values)


def _rule(days: int = 1) -> DateOffsetRule:
    return DateOffsetRule(default_calendar("in"), max_business_days=days)


def test_friday_to_monday_is_one_business_day_and_matches_at_t_plus_1() -> None:
    result = _rule(1).evaluate(_txn(date(2026, 3, 13)), _txn(date(2026, 3, 16)))
    assert result.matched is True
    assert "1 business day" in result.explanation


def test_a_longer_gap_than_the_bank_window_is_declined() -> None:
    result = _rule(1).evaluate(_txn(date(2026, 3, 13)), _txn(date(2026, 3, 17)))
    assert (result.matched, "2 business days" in result.explanation) == (False, True)


def test_a_holiday_does_not_count_against_the_window() -> None:
    # Fri 23 Jan -> Tue 27 Jan spans the Monday holiday: one business day
    assert _rule(1).evaluate(_txn(date(2026, 1, 23)), _txn(date(2026, 1, 27))).matched is True


def test_the_same_day_is_left_to_the_exact_matcher() -> None:
    result = _rule().evaluate(_txn(date(2026, 3, 16)), _txn(date(2026, 3, 16)))
    assert (result.matched, "same business day" in result.explanation) == (False, True)


@pytest.mark.parametrize(
    "override",
    [
        {"amount_minor": 10_001},
        {"direction": "DR"},
        {"currency": "USD"},
        {"normalised_reference": "OTHER"},
        {"normalised_reference": None},
    ],
)
def test_anything_but_the_date_differing_is_declined(override: dict[str, Any]) -> None:
    result = _rule().evaluate(_txn(date(2026, 3, 13)), _txn(date(2026, 3, 16), **override))
    assert result.matched is False


def test_invalid_construction_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        DateOffsetRule(default_calendar("in"), max_business_days=-1)
    with pytest.raises(ValueError, match="confidence"):
        DateOffsetRule(default_calendar("in"), max_business_days=1, confidence=0)
