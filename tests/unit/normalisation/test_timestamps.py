# tests/unit/normalisation/test_timestamps.py
"""Tests for recon.normalisation.timestamps.

test_ist_2330_does_not_roll_over_to_next_day_in_utc is the direct,
permanent regression guard against A5.2's own worked example being wrong
(AE-05) — this is the single most important assertion in this file.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from recon.normalisation.timestamps import (
    TimestampNormalisationError,
    normalise_mt940_date,
    normalise_timestamp,
    to_utc_date,
)


def test_simple_date_only_normalisation_assumes_midnight_local() -> None:
    result = normalise_timestamp("15-03-2026", "%d-%m-%Y", "Asia/Kolkata")

    # midnight IST (00:00 +05:30) = 18:30 UTC the PREVIOUS day
    assert result.utc == datetime(2026, 3, 14, 18, 30, tzinfo=UTC)
    assert result.original_text == "15-03-2026"
    assert result.source_timezone == "Asia/Kolkata"


def test_ist_2330_does_not_roll_over_to_next_day_in_utc() -> None:
    """AE-05: A5.2's own example claims 11:30 PM IST on 15 March becomes
    16 March in UTC+0. It doesn't — 23:30 IST = 18:00 UTC, still the 15th.
    This test pins the arithmetically correct behaviour permanently."""
    result = normalise_timestamp(
        "15-03-2026", "%d-%m-%Y", "Asia/Kolkata", time_text="2330", time_format="%H%M"
    )

    assert result.utc == datetime(2026, 3, 15, 18, 0, tzinfo=UTC)
    assert result.utc.date() == date(2026, 3, 15)  # still the 15th, not the 16th


def test_singapore_utc_plus_8_does_roll_over_for_a_late_transaction() -> None:
    """A5.2's Singapore claim (UTC+8, so the same transaction becomes the
    16th) IS arithmetically correct, unlike the London claim — confirming
    this module gets the case that should roll over right too, not just
    the case that shouldn't."""
    result = normalise_timestamp(
        "15-03-2026", "%d-%m-%Y", "Asia/Kolkata", time_text="2330", time_format="%H%M"
    )
    singapore_local = result.utc.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Singapore"))

    assert singapore_local.date() == date(2026, 3, 16)


def test_uk_dst_transition_is_handled_by_zoneinfo() -> None:
    """India does not observe DST; UK does. A date either side of the UK's
    spring-forward transition must convert correctly without special-case
    code — zoneinfo handles this natively, this test just confirms it."""
    before_dst = normalise_timestamp("28-03-2026", "%d-%m-%Y", "Europe/London")
    after_dst = normalise_timestamp("30-03-2026", "%d-%m-%Y", "Europe/London")

    # GMT (UTC+0) before the transition, BST (UTC+1) after
    assert before_dst.utc.hour == 0
    assert after_dst.utc.hour == 23  # midnight BST on the 30th = 23:00 UTC on the 29th
    assert after_dst.utc.date() == date(2026, 3, 29)


def test_invalid_date_format_raises() -> None:
    with pytest.raises(TimestampNormalisationError, match="does not match"):
        normalise_timestamp("2026/03/15", "%d-%m-%Y", "Asia/Kolkata")


def test_invalid_calendar_date_raises() -> None:
    with pytest.raises(TimestampNormalisationError):
        normalise_timestamp("31-02-2026", "%d-%m-%Y", "Asia/Kolkata")


def test_unknown_timezone_raises() -> None:
    with pytest.raises(TimestampNormalisationError, match="not a known IANA timezone"):
        normalise_timestamp("15-03-2026", "%d-%m-%Y", "Not/A_Real_Zone")


def test_invalid_time_text_raises() -> None:
    with pytest.raises(TimestampNormalisationError, match="does not match expected time"):
        normalise_timestamp("15-03-2026", "%d-%m-%Y", "Asia/Kolkata", time_text="not-a-time")


def test_normalise_mt940_date_yymmdd() -> None:
    result = normalise_mt940_date("260315", "Asia/Kolkata")
    assert result.utc.date() == date(2026, 3, 14)  # midnight IST -> previous day UTC


def test_to_utc_date_extracts_calendar_date() -> None:
    result = normalise_timestamp("15-03-2026", "%d-%m-%Y", "Asia/Kolkata")
    assert to_utc_date(result) == date(2026, 3, 14)
