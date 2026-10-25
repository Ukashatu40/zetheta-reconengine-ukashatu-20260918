# src/recon/calendar.py
"""Business-day calendar (A3.3's Date Offset Rule): weekends plus holidays
loaded from config/calendars/<name>-<year>.yaml.

A date outside every loaded year file is treated with weekends only, never
silently as all-business-days-with-holidays; callers can check
covers(date) to know whether holidays were actually available.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from recon.paths import config_dir

_SATURDAY = 5


class CalendarConfigError(ValueError):
    """A calendar file is malformed."""


@dataclass(frozen=True, slots=True)
class BusinessCalendar:
    name: str
    holidays: frozenset[date]
    years: frozenset[int]

    def covers(self, day: date) -> bool:
        return day.year in self.years

    def is_business_day(self, day: date) -> bool:
        return day.weekday() < _SATURDAY and day not in self.holidays

    def business_days_between(self, start: date, end: date) -> int:
        """Business days after the earlier date up to and including the later
        one, so Friday to Monday is 1 and a date to itself is 0."""
        if end < start:
            start, end = end, start
        count = 0
        day = start
        while day < end:
            day += timedelta(days=1)
            if self.is_business_day(day):
                count += 1
        return count

    def add_business_days(self, start: date, days: int) -> date:
        if days < 0:
            raise ValueError("days must be non-negative")
        day = start
        remaining = days
        while remaining:
            day += timedelta(days=1)
            if self.is_business_day(day):
                remaining -= 1
        return day


def _parse(path: Path) -> tuple[str, int, set[date]]:
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "calendar" not in data or "year" not in data:
        raise CalendarConfigError(f"{path}: expected 'calendar' and 'year' keys")
    try:
        holidays = {date.fromisoformat(str(h["date"])) for h in data.get("holidays", [])}
    except (KeyError, TypeError, ValueError) as exc:
        raise CalendarConfigError(f"{path}: bad holiday entry: {exc}") from exc
    year = int(data["year"])
    stray = sorted(h for h in holidays if h.year != year)
    if stray:
        raise CalendarConfigError(f"{path}: holidays outside {year}: {stray}")
    return str(data["calendar"]), year, holidays


def load_calendar(name: str, directory: Path | None = None) -> BusinessCalendar:
    directory = directory if directory is not None else config_dir() / "calendars"
    holidays: set[date] = set()
    years: set[int] = set()
    for path in sorted(directory.glob(f"{name}-*.yaml")):
        calendar_name, year, found = _parse(path)
        if calendar_name != name:
            raise CalendarConfigError(f"{path}: declares calendar {calendar_name!r}, not {name!r}")
        years.add(year)
        holidays |= found
    return BusinessCalendar(name, frozenset(holidays), frozenset(years))


@lru_cache(maxsize=8)
def default_calendar(name: str = "in") -> BusinessCalendar:
    return load_calendar(name)
