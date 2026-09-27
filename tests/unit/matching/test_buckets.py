# tests/unit/matching/test_buckets.py
"""Tests for recon.matching.blocking.buckets."""

from __future__ import annotations

from datetime import date

import pytest

from recon.matching.blocking.buckets import (
    amount_bucket,
    counterparty_key,
    date_bucket,
    reference_prefix,
)


def test_amounts_within_the_same_bucket_width_land_together() -> None:
    assert amount_bucket(150_000, 10_000) == amount_bucket(150_500, 10_000)


def test_amounts_across_a_bucket_boundary_land_apart() -> None:
    assert amount_bucket(149_999, 10_000) != amount_bucket(150_000, 10_000)


def test_amount_bucket_rejects_non_positive_width() -> None:
    with pytest.raises(ValueError, match="positive"):
        amount_bucket(1000, 0)


def test_dates_within_the_same_window_land_together() -> None:
    assert date_bucket(date(2026, 3, 15), 7) == date_bucket(date(2026, 3, 16), 7)


def test_date_bucket_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError, match="positive"):
        date_bucket(date(2026, 3, 15), 0)


def test_reference_prefix_truncates_to_requested_length() -> None:
    assert reference_prefix("REF0001234567", 6) == "REF000"


def test_reference_prefix_shorter_than_requested_length_returns_whole_string() -> None:
    assert reference_prefix("REF1", 10) == "REF1"


def test_counterparty_key_returns_prefix_when_present() -> None:
    assert counterparty_key("ACME CORPORATION", 8) == "ACME COR"


def test_counterparty_key_returns_none_when_name_is_absent() -> None:
    """AE-01: MT940 sources never produce a counterparty name. Pass C
    must be skippable for such transactions, not blocked on an empty
    string."""
    assert counterparty_key(None, 8) is None


def test_counterparty_key_returns_none_for_empty_string() -> None:
    assert counterparty_key("", 8) is None
