# src/recon/anomaly/stats.py
"""Rolling statistics with Welford's online algorithm.

Keeps a running mean and the sum of squared deviations (m2) so the standard
deviation can be updated one amount at a time without storing history, and
without the catastrophic cancellation of the sum-of-squares formula. All values
are Decimal on integer minor units; nothing here touches float.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RollingStats:
    count: int
    mean: Decimal
    m2: Decimal
    active_days: int
    last_active_date: date

    @property
    def variance(self) -> Decimal:
        """Sample variance; zero until there are two observations."""
        return self.m2 / (self.count - 1) if self.count > 1 else Decimal(0)

    @property
    def std_dev(self) -> Decimal:
        return self.variance.sqrt()

    @property
    def daily_average_count(self) -> Decimal:
        """Mean transactions per active day."""
        return Decimal(self.count) / Decimal(self.active_days)


def first_observation(amount_minor: int, day: date) -> RollingStats:
    return RollingStats(1, Decimal(amount_minor), Decimal(0), 1, day)


def observe(stats: RollingStats, amount_minor: int, day: date) -> RollingStats:
    """The statistics after one more amount on `day`. Days must not go backwards:
    an out-of-order day is treated as the same active day rather than a new one."""
    count = stats.count + 1
    delta = Decimal(amount_minor) - stats.mean
    mean = stats.mean + delta / count
    m2 = stats.m2 + delta * (Decimal(amount_minor) - mean)
    new_day = day > stats.last_active_date
    return RollingStats(
        count,
        mean,
        m2,
        stats.active_days + (1 if new_day else 0),
        day if new_day else stats.last_active_date,
    )
