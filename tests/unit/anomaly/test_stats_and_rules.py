"""Rolling statistics and the pure anomaly rules (Case Study C2)."""

from __future__ import annotations

import random
import statistics
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from recon.anomaly.config import (
    AbsenceConfig,
    AmountDeviationConfig,
    AnomalyConfigError,
    VelocityConfig,
    load_anomaly_config,
)
from recon.anomaly.rules import (
    amount_deviation,
    expected_settlement_overdue,
    is_date_only,
    peak_window,
    velocity_breach,
)
from recon.anomaly.stats import RollingStats, first_observation, observe
from recon.calendar import default_calendar

_DAY = date(2026, 3, 16)
_AMOUNT = AmountDeviationConfig(sigma=Decimal(3), min_history=30, whale_multiplier=Decimal(100))
_VELOCITY = VelocityConfig(multiplier=Decimal(2), window_minutes=60, min_count=3, min_active_days=3)


def _history(amounts: list[int]) -> RollingStats:
    stats = first_observation(amounts[0], _DAY)
    for amount in amounts[1:]:
        stats = observe(stats, amount, _DAY)
    return stats


# -- Welford ---------------------------------------------------------------------


def test_the_running_mean_and_deviation_match_the_statistics_module() -> None:
    rng = random.Random(7)
    amounts = [rng.randint(1_000, 900_000) for _ in range(500)]
    stats = _history(amounts)
    assert stats.count == 500
    assert float(stats.mean) == pytest.approx(statistics.fmean(amounts), rel=1e-12)
    assert float(stats.std_dev) == pytest.approx(statistics.stdev(amounts), rel=1e-9)


def test_one_observation_has_no_spread() -> None:
    stats = first_observation(5_000, _DAY)
    assert stats.count == 1
    assert (stats.mean, stats.variance, stats.std_dev) == (Decimal(5_000), Decimal(0), Decimal(0))


def test_active_days_count_distinct_days_and_ignore_out_of_order_ones() -> None:
    stats = first_observation(100, _DAY)
    stats = observe(stats, 100, _DAY)  # same day
    stats = observe(stats, 100, _DAY + timedelta(days=1))
    stats = observe(stats, 100, _DAY)  # a late arrival for an earlier day
    assert (stats.count, stats.active_days, stats.last_active_date) == (
        4,
        2,
        _DAY + timedelta(days=1),
    )
    assert stats.daily_average_count == 2


def test_the_stats_are_exact_decimals_not_floats() -> None:
    stats = _history([10, 20, 30])
    assert isinstance(stats.mean, Decimal) and stats.mean == 20
    assert stats.variance == 100


# -- amount deviation ----------------------------------------------------------------


def _steady(n: int = 40, around: int = 100_000) -> RollingStats:
    return _history([around + (i % 7) * 100 for i in range(n)])


def test_an_ordinary_amount_is_not_flagged() -> None:
    assert amount_deviation(100_300, _steady(), _AMOUNT) is None


def test_an_amount_beyond_three_sigma_is_flagged_with_its_z_score() -> None:
    history = _steady()
    found = amount_deviation(105_000, history, _AMOUNT)
    assert found is not None and found.z_score is not None
    assert found.z_score > 3 and found.is_whale is False


def test_three_sigma_is_a_strict_threshold() -> None:
    history = _steady()
    edge = int(history.mean + 3 * history.std_dev)  # just under 3 sigma after truncation
    assert amount_deviation(edge, history, _AMOUNT) is None


def test_too_little_history_never_flags() -> None:
    assert amount_deviation(10**9, _steady(n=29), _AMOUNT) is None
    assert amount_deviation(10**9, None, _AMOUNT) is None


def test_a_whale_is_flagged_even_when_the_history_is_noisy() -> None:
    noisy = _history([1_000 * (i + 1) for i in range(40)])  # mean 20,500, big spread
    found = amount_deviation(3_000_000, noisy, _AMOUNT)  # 146x the mean
    assert found is not None and found.is_whale is True


def test_a_counterparty_that_always_pays_the_same_amount_flags_any_other() -> None:
    constant = _history([250_000] * 40)
    broken = amount_deviation(250_001, constant, _AMOUNT)
    assert broken is not None and broken.z_score is None
    assert amount_deviation(250_000, constant, _AMOUNT) is None


def test_a_non_positive_mean_is_ignored() -> None:
    assert amount_deviation(100, _history([0] * 40), _AMOUNT) is None


# -- velocity ---------------------------------------------------------------------


def _at(minutes: int) -> datetime:
    return datetime(2026, 3, 16, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def test_peak_window_finds_the_densest_hour() -> None:
    stamps = [_at(0), _at(10), _at(20), _at(200), _at(210)]
    assert peak_window(stamps, timedelta(hours=1)) == (3, _at(0))


def test_the_window_is_open_at_the_end() -> None:
    assert peak_window([_at(0), _at(60)], timedelta(hours=1))[0] == 1  # type: ignore[index]
    assert peak_window([_at(0), _at(59)], timedelta(hours=1))[0] == 2  # type: ignore[index]


def test_peak_window_of_nothing_is_none() -> None:
    assert peak_window([], timedelta(hours=1)) is None


def _daily(count: int, days: int) -> RollingStats:
    stats = first_observation(1_000, _DAY)
    for i in range(1, count):
        stats = observe(stats, 1_000, _DAY + timedelta(days=i * days // count))
    return stats


def test_more_than_twice_the_daily_average_in_an_hour_is_a_breach() -> None:
    history = _daily(count=20, days=10)  # about 2 a day
    assert 1.9 < float(history.daily_average_count) < 2.1
    breach = velocity_breach([_at(i) for i in range(0, 25, 5)], history, _VELOCITY)  # 5 in an hour
    assert breach is not None and breach.count == 5


def test_a_busy_hour_below_the_multiple_is_fine() -> None:
    history = _daily(count=20, days=2)  # about 10 a day
    assert velocity_breach([_at(0), _at(5), _at(10)], history, _VELOCITY) is None


def test_the_minimum_count_stops_a_tiny_burst_alerting() -> None:
    history = _daily(count=5, days=5)  # one a day: two in an hour is "200%" but only two
    assert velocity_breach([_at(0), _at(5)], history, _VELOCITY) is None


def test_velocity_needs_enough_active_days_of_history() -> None:
    young = first_observation(1_000, _DAY)
    assert velocity_breach([_at(i) for i in range(10)], young, _VELOCITY) is None
    assert velocity_breach([_at(i) for i in range(10)], None, _VELOCITY) is None


@pytest.mark.parametrize(
    ("original", "date_only"),
    [("15-03-2026", True), ("2026-03-15T10:30:00", False), ("15/03/2026 10:30", False)],
)
def test_is_date_only(original: str, date_only: bool) -> None:
    assert is_date_only(original) is date_only


# -- absence --------------------------------------------------------------------------


_IST = "Asia/Kolkata"
_CAL = default_calendar("in")
_GRACE = AbsenceConfig(grace_minutes=120)


def _ist(hour: int, minute: int, day: date = _DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(_IST)).astimezone(
        UTC
    )


def test_overdue_beyond_the_grace_returns_the_due_moment() -> None:
    found = expected_settlement_overdue(
        _ist(20, 1), expected_time="18:00", timezone=_IST, calendar=_CAL, cfg=_GRACE
    )
    assert found is not None
    day, due = found
    assert (day, due) == (_DAY, _ist(18, 0))


def test_within_the_grace_period_it_is_not_overdue() -> None:
    assert (
        expected_settlement_overdue(
            _ist(19, 59), expected_time="18:00", timezone=_IST, calendar=_CAL, cfg=_GRACE
        )
        is None
    )
    assert (
        expected_settlement_overdue(
            _ist(20, 0), expected_time="18:00", timezone=_IST, calendar=_CAL, cfg=_GRACE
        )
        is None
    )  # exactly at the grace boundary


def test_weekends_and_holidays_never_expect_a_settlement() -> None:
    sunday = date(2026, 3, 15)
    republic_day = date(2026, 1, 26)
    for day in (sunday, republic_day):
        assert (
            expected_settlement_overdue(
                _ist(23, 0, day), expected_time="18:00", timezone=_IST, calendar=_CAL, cfg=_GRACE
            )
            is None
        )


def test_the_day_is_the_banks_local_day_not_utc() -> None:
    # 23:30 UTC on Mon 16 March is 05:00 IST on Tue 17: the due time for the 17th has not come
    now = datetime(2026, 3, 16, 23, 30, tzinfo=UTC)
    assert (
        expected_settlement_overdue(
            now, expected_time="18:00", timezone=_IST, calendar=_CAL, cfg=_GRACE
        )
        is None
    )


# -- config ---------------------------------------------------------------------------


def test_the_shipped_config_loads_with_the_day_5_numbers() -> None:
    cfg = load_anomaly_config()
    assert (cfg.amount_deviation.sigma, cfg.velocity.multiplier, cfg.absence.grace_minutes) == (
        Decimal(3),
        Decimal(2),
        120,
    )
    assert (cfg.match_rate.threshold, cfg.exception_rate.threshold) == (
        Decimal("0.995"),
        Decimal("0.05"),
    )


def test_environment_variables_override_the_two_rate_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SUSPICIOUS_MATCH_RATE_THRESHOLD", "0.99")
    monkeypatch.setenv("EXCEPTION_RATE_ALERT_THRESHOLD", "0.1")
    cfg = load_anomaly_config()
    assert (cfg.match_rate.threshold, cfg.exception_rate.threshold) == (
        Decimal("0.99"),
        Decimal("0.1"),
    )
    assert cfg.match_rate.min_externals == 50  # the rest of the section is untouched


def test_a_bad_override_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUSPICIOUS_MATCH_RATE_THRESHOLD", "lots")
    with pytest.raises(AnomalyConfigError, match="not a number"):
        load_anomaly_config()


def test_a_malformed_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text("- nope\n")
    with pytest.raises(AnomalyConfigError, match="mapping"):
        load_anomaly_config(path)
    path.write_text("config_version: x\n")
    with pytest.raises(AnomalyConfigError):
        load_anomaly_config(path)
