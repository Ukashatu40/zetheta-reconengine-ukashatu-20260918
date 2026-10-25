"""Business-day calendar: weekends, holidays, loading and validation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from recon.calendar import CalendarConfigError, default_calendar, load_calendar

_REPUBLIC_DAY = date(2026, 1, 26)  # a Monday


def test_the_shipped_calendar_loads_with_2026_holidays() -> None:
    calendar = default_calendar("in")
    assert calendar.covers(date(2026, 6, 1))
    assert not calendar.covers(date(2027, 1, 1))
    assert not calendar.is_business_day(_REPUBLIC_DAY)
    assert calendar.is_business_day(date(2026, 1, 27))


def test_weekends_are_not_business_days() -> None:
    calendar = default_calendar("in")
    assert [calendar.is_business_day(date(2026, 3, d)) for d in (13, 14, 15, 16)] == [
        True,
        False,
        False,
        True,
    ]


def test_friday_to_monday_is_one_business_day() -> None:
    calendar = default_calendar("in")
    assert calendar.business_days_between(date(2026, 3, 13), date(2026, 3, 16)) == 1


def test_a_date_to_itself_is_zero_and_the_order_does_not_matter() -> None:
    calendar = default_calendar("in")
    assert calendar.business_days_between(date(2026, 3, 16), date(2026, 3, 16)) == 0
    assert calendar.business_days_between(date(2026, 3, 18), date(2026, 3, 16)) == 2


def test_a_holiday_is_skipped_when_counting() -> None:
    calendar = default_calendar("in")
    # Fri 23 Jan -> Tue 27 Jan: Mon 26 Jan is Republic Day, so only Tue counts.
    assert calendar.business_days_between(date(2026, 1, 23), date(2026, 1, 27)) == 1


def test_add_business_days_skips_weekends_and_holidays() -> None:
    calendar = default_calendar("in")
    assert calendar.add_business_days(date(2026, 1, 23), 1) == date(2026, 1, 27)
    assert calendar.add_business_days(date(2026, 3, 13), 3) == date(2026, 3, 18)
    assert calendar.add_business_days(date(2026, 3, 13), 0) == date(2026, 3, 13)


def test_add_business_days_rejects_a_negative_count() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        default_calendar("in").add_business_days(date(2026, 3, 13), -1)


def test_an_unknown_calendar_has_no_holidays_and_covers_no_year(tmp_path: Path) -> None:
    calendar = load_calendar("zz", tmp_path)
    assert not calendar.covers(date(2026, 1, 1))
    assert calendar.is_business_day(_REPUBLIC_DAY)


def test_a_holiday_outside_the_declared_year_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "xx-2026.yaml").write_text(
        'calendar: xx\nyear: 2026\nholidays:\n  - { date: "2027-01-01", name: stray }\n'
    )
    with pytest.raises(CalendarConfigError, match="outside 2026"):
        load_calendar("xx", tmp_path)


def test_a_file_declaring_another_calendar_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "xx-2026.yaml").write_text("calendar: yy\nyear: 2026\nholidays: []\n")
    with pytest.raises(CalendarConfigError, match="not 'xx'"):
        load_calendar("xx", tmp_path)


def test_a_malformed_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "xx-2026.yaml").write_text("- just a list\n")
    with pytest.raises(CalendarConfigError, match="expected"):
        load_calendar("xx", tmp_path)
    (tmp_path / "xx-2026.yaml").write_text(
        "calendar: xx\nyear: 2026\nholidays:\n  - { date: not-a-date }\n"
    )
    with pytest.raises(CalendarConfigError, match="bad holiday"):
        load_calendar("xx", tmp_path)
