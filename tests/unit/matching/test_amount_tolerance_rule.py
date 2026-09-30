# tests/unit/matching/test_amount_tolerance_rule.py
"""Tests for recon.matching.rules.amount_tolerance.AmountToleranceRule."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from recon.matching.rules.amount_tolerance import AmountToleranceRule
from recon.persistence.models import NormalisedTransaction


def _txn(**overrides: object) -> NormalisedTransaction:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "txn_date": date(2026, 3, 15),
        "raw_transaction_id": uuid.uuid4(),
        "ingestion_file_id": uuid.uuid4(),
        "source": "INTERNAL",
        "bank_code": "HDFC",
        "format_type": "CSV",
        "config_version": "hdfc.v1",
        "txn_id": "REF001",
        "amount": Decimal("1500.00"),
        "amount_minor": 150_000,
        "currency": "INR",
        "currency_exponent": 2,
        "currency_source": "ENTRY_LEVEL",
        "direction": "CR",
        "is_reversal": False,
        "txn_timestamp_utc": datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        "txn_timestamp_original": "15-03-2026",
        "source_timezone": "Asia/Kolkata",
        "normalised_reference": "REF001",
        "match_status": "UNMATCHED",
    }
    defaults.update(overrides)
    return NormalisedTransaction(**defaults)


def _pair(**external_overrides: object) -> tuple[NormalisedTransaction, NormalisedTransaction]:
    return _txn(), _txn(id=uuid.uuid4(), source="EXTERNAL", **external_overrides)


def test_amount_within_tolerance_matches() -> None:
    internal, external = _pair(amount_minor=150_001)
    result = AmountToleranceRule().evaluate(internal, external)
    assert result.matched is True
    assert result.confidence_contribution == 0.90


def test_difference_exactly_at_tolerance_matches() -> None:
    internal, external = _pair(amount_minor=150_005)
    assert AmountToleranceRule(default_tolerance_minor=5).evaluate(internal, external).matched


def test_difference_one_above_tolerance_does_not_match() -> None:
    internal, external = _pair(amount_minor=150_006)
    assert not AmountToleranceRule(default_tolerance_minor=5).evaluate(internal, external).matched


def test_different_currency_is_declined_not_compared() -> None:
    internal, external = _pair(currency="USD")
    result = AmountToleranceRule().evaluate(internal, external)
    assert result.matched is False
    assert "cross-currency" in result.explanation


def test_different_direction_does_not_match() -> None:
    internal, external = _pair(direction="DR")
    assert not AmountToleranceRule().evaluate(internal, external).matched


def test_different_reference_does_not_match_even_with_identical_amount() -> None:
    """The safety property: amount agreement alone must never match."""
    internal, external = _pair(normalised_reference="OTHER")
    assert not AmountToleranceRule().evaluate(internal, external).matched


def test_missing_reference_does_not_match() -> None:
    internal, external = _pair(normalised_reference=None)
    assert not AmountToleranceRule().evaluate(internal, external).matched


def test_dates_three_days_apart_match_and_four_do_not() -> None:
    internal, three = _pair(txn_date=date(2026, 3, 18))
    _, four = _pair(txn_date=date(2026, 3, 19))
    rule = AmountToleranceRule()
    assert rule.evaluate(internal, three).matched
    assert not rule.evaluate(internal, four).matched


def test_per_currency_override_applies() -> None:
    internal, external = _pair(currency="JPY", amount_minor=150_001)
    rule = AmountToleranceRule(default_tolerance_minor=1, tolerance_minor_by_currency={"JPY": 0})
    assert not rule.evaluate(internal, external).matched


def test_negative_tolerance_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        AmountToleranceRule(default_tolerance_minor=-1)


def test_confidence_outside_unit_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"\(0, 1\]"):
        AmountToleranceRule(confidence=0.0)
