# src/recon/anomaly/rules.py
"""The detection rules themselves, pure and free of I/O (Case Study C2).

Amount Deviation  an amount more than `sigma` standard deviations from the
                  mean for the same counterparty and direction, or a whale: over
                  `whale_multiplier` times the mean.
Velocity          more than `multiplier` times the counterparty's daily-average
                  count inside any one `window_minutes` window.
Absence           an expected settlement more than `grace_minutes` late.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from recon.anomaly.config import AbsenceConfig, AmountDeviationConfig, VelocityConfig
from recon.anomaly.stats import RollingStats
from recon.calendar import BusinessCalendar


@dataclass(frozen=True, slots=True)
class Deviation:
    z_score: Decimal | None  # None when the history has no spread at all
    multiple_of_mean: Decimal
    is_whale: bool


@dataclass(frozen=True, slots=True)
class VelocityBreach:
    count: int
    window_start: datetime
    daily_average: Decimal


def amount_deviation(
    amount_minor: int, history: RollingStats | None, cfg: AmountDeviationConfig
) -> Deviation | None:
    """None when the amount is unremarkable or there is too little history."""
    if history is None or history.count < cfg.min_history or history.mean <= 0:
        return None
    amount = Decimal(amount_minor)
    multiple = amount / history.mean
    whale = multiple > cfg.whale_multiplier
    spread = history.std_dev
    if spread == 0:
        # The counterparty has only ever paid one amount; any other amount is a break.
        return Deviation(None, multiple, whale) if amount != history.mean else None
    z = abs(amount - history.mean) / spread
    if z > cfg.sigma or whale:
        return Deviation(z, multiple, whale)
    return None


def peak_window(timestamps: Sequence[datetime], window: timedelta) -> tuple[int, datetime] | None:
    """The largest number of timestamps inside any window of this length, and where
    it starts. The window is closed at the start and open at the end."""
    if not timestamps:
        return None
    ordered = sorted(timestamps)
    best = (0, ordered[0])
    left = 0
    for right, stamp in enumerate(ordered):
        while stamp - ordered[left] >= window:
            left += 1
        if right - left + 1 > best[0]:
            best = (right - left + 1, ordered[left])
    return best


def velocity_breach(
    timestamps: Sequence[datetime], history: RollingStats | None, cfg: VelocityConfig
) -> VelocityBreach | None:
    if history is None or history.active_days < cfg.min_active_days:
        return None
    peak = peak_window(timestamps, timedelta(minutes=cfg.window_minutes))
    if peak is None:
        return None
    count, start = peak
    daily = history.daily_average_count
    if count >= cfg.min_count and Decimal(count) > cfg.multiplier * daily:
        return VelocityBreach(count, start, daily)
    return None


def is_date_only(original_timestamp: str) -> bool:
    """A source value with no clock time (a bare date) carries no hour, so an hourly
    rate cannot be computed from it."""
    return ":" not in original_timestamp


def expected_settlement_overdue(
    now: datetime,
    *,
    expected_time: str,
    timezone: str,
    calendar: BusinessCalendar,
    cfg: AbsenceConfig,
) -> tuple[date, datetime] | None:
    """(local business day, the moment the settlement was due) if `now` is more than
    the grace past it, else None. Non-business days never expect a settlement."""
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone)
    today = local_now.date()
    if not calendar.is_business_day(today):
        return None
    hour, minute = (int(part) for part in expected_time.split(":"))
    due = datetime.combine(today, time(hour, minute), tzinfo=zone)
    if local_now > due + timedelta(minutes=cfg.grace_minutes):
        return today, due.astimezone(UTC)
    return None
