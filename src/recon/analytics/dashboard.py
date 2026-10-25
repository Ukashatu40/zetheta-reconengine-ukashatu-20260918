# src/recon/analytics/dashboard.py
"""Read-side queries behind the dashboard endpoints (Day 5 D-series).

Everything here is a plain aggregate over existing tables; nothing mutates.
Counts are by normalised transaction. A "matched" transaction is one whose
match_status is MATCHED, so PENDING_REVIEW pairs do not inflate any rate (AE-10).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import ColumnElement, Date, case, cast, func, select
from sqlalchemy.orm import Session

from recon.persistence.models import (
    MatchResult,
    NormalisedTransaction,
    ReconciliationRun,
    ReconException,
)

OPEN_STATUSES = ("OPEN", "IN_REVIEW", "ESCALATED", "REOPENED")
TREND_DAYS = 7


@dataclass(frozen=True, slots=True)
class DailyPoint:
    day: date
    total_transactions: int
    matched_count: int
    match_rate: float
    exception_count: int


@dataclass(frozen=True, slots=True)
class BankStats:
    bank_code: str
    total_transactions: int
    matched_count: int
    match_rate: float
    exception_count: int
    open_exception_count: int
    exception_rate: float
    avg_settlement_days: float | None
    sla_compliance_percent: float


@dataclass(frozen=True, slots=True)
class HourPoint:
    hour: int
    transaction_count: int
    matched_count: int
    match_rate: float


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _matched() -> ColumnElement[int]:
    return func.count().filter(NormalisedTransaction.match_status == "MATCHED")


def count_where(session: Session, entity: type, *conditions: ColumnElement[bool]) -> int:
    return session.scalar(select(func.count()).select_from(entity).where(*conditions)) or 0


def average_processing_seconds(session: Session) -> float | None:
    """Mean wall-clock duration of COMPLETED runs, in seconds."""
    seconds = session.scalar(
        select(
            func.avg(
                func.extract("epoch", ReconciliationRun.completed_at - ReconciliationRun.started_at)
            )
        ).where(
            ReconciliationRun.status == "COMPLETED",
            ReconciliationRun.started_at.is_not(None),
            ReconciliationRun.completed_at.is_not(None),
        )
    )
    return round(float(seconds), 3) if seconds is not None else None


def daily_trend(session: Session, as_of: date, days: int = TREND_DAYS) -> list[DailyPoint]:
    start = as_of - timedelta(days=days - 1)
    txn_rows = {
        row.txn_date: (row.total, row.matched)
        for row in session.execute(
            select(
                NormalisedTransaction.txn_date,
                func.count().label("total"),
                _matched().label("matched"),
            )
            .where(NormalisedTransaction.txn_date.between(start, as_of))
            .group_by(NormalisedTransaction.txn_date)
        )
    }
    created_day = cast(ReconException.created_at, Date)
    exception_rows = {
        row.day: row.n
        for row in session.execute(
            select(created_day.label("day"), func.count().label("n"))
            .where(created_day.between(start, as_of))
            .group_by(created_day)
        )
    }
    points: list[DailyPoint] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        total, matched = txn_rows.get(day, (0, 0))
        points.append(
            DailyPoint(day, total, matched, _rate(matched, total), exception_rows.get(day, 0))
        )
    return points


def bank_breakdown(session: Session, bank_codes: list[str], now: datetime) -> list[BankStats]:
    txn = {
        row.bank_code: row
        for row in session.execute(
            select(
                NormalisedTransaction.bank_code,
                func.count().label("total"),
                _matched().label("matched"),
                func.avg(NormalisedTransaction.settlement_date - NormalisedTransaction.txn_date)
                .filter(
                    NormalisedTransaction.source == "EXTERNAL",
                    NormalisedTransaction.settlement_date.is_not(None),
                )
                .label("avg_settlement"),
            ).group_by(NormalisedTransaction.bank_code)
        )
    }
    on_time = case(
        (
            ReconException.resolved_at.is_not(None),
            ReconException.resolved_at <= ReconException.sla_deadline,
        ),
        else_=(ReconException.sla_breached_at.is_(None)) & (ReconException.sla_deadline >= now),
    )
    exc = {
        row.bank_code: row
        for row in session.execute(
            select(
                ReconException.bank_code,
                func.count().label("total"),
                func.count().filter(ReconException.status.in_(OPEN_STATUSES)).label("open"),
                func.count().filter(on_time).label("on_time"),
            ).group_by(ReconException.bank_code)
        )
    }
    codes = sorted(set(bank_codes) | set(txn) | set(exc))
    stats: list[BankStats] = []
    for code in codes:
        t = txn.get(code)
        e = exc.get(code)
        total = t.total if t else 0
        matched = t.matched if t else 0
        exceptions = e.total if e else 0
        stats.append(
            BankStats(
                bank_code=code,
                total_transactions=total,
                matched_count=matched,
                match_rate=_rate(matched, total),
                exception_count=exceptions,
                open_exception_count=e.open if e else 0,
                exception_rate=_rate(exceptions, total),
                avg_settlement_days=(
                    round(float(t.avg_settlement), 2)
                    if t and t.avg_settlement is not None
                    else None
                ),
                sla_compliance_percent=(
                    round(100 * e.on_time / exceptions, 2) if e and exceptions else 100.0
                ),
            )
        )
    return stats


def hourly_timeline(session: Session, day: date) -> list[HourPoint]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    hour = func.extract("hour", NormalisedTransaction.txn_timestamp_utc)
    rows = {
        int(row.hour): (row.total, row.matched)
        for row in session.execute(
            select(hour.label("hour"), func.count().label("total"), _matched().label("matched"))
            .where(
                NormalisedTransaction.txn_timestamp_utc >= start,
                NormalisedTransaction.txn_timestamp_utc < start + timedelta(days=1),
            )
            .group_by(hour)
        )
    }
    points: list[HourPoint] = []
    for h in range(24):
        total, matched = rows.get(h, (0, 0))
        points.append(HourPoint(h, total, matched, _rate(matched, total)))
    return points


def pending_review_count(session: Session) -> int:
    return count_where(session, MatchResult, MatchResult.status == "PENDING_REVIEW")
