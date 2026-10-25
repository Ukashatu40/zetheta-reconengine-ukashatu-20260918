# src/recon/api/v1/dashboard.py
"""Dashboard endpoints (Day 5): summary, per-bank breakdown, hourly timeline.

Responses are cached for DASHBOARD_CACHE_TTL_SECONDS (default 30, R78). They are
the same for every viewer, so the cache key carries only the query parameters.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, date
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute

from recon import cache as cache_module
from recon.analytics import dashboard as queries
from recon.api.auth import require_viewer
from recon.api.deps import NowDep, SessionDep
from recon.api.v1.runs import get_run_settings
from recon.api.v1.schemas.responses import (
    BankDashboardRow,
    BanksDashboard,
    DailyTrendPoint,
    DashboardSummary,
    TimelineDashboard,
    TimelineHour,
)
from recon.persistence.models import Alert, NormalisedTransaction, ReconException
from recon.runs.settings import RunSettings

router = APIRouter(
    prefix="/api/v1/dashboard", tags=["dashboard"], dependencies=[Depends(require_viewer)]
)

M = TypeVar("M", bound=BaseModel)


def _ttl() -> int:
    return int(os.environ.get("DASHBOARD_CACHE_TTL_SECONDS", "30"))


def _cached(key: str, model: type[M], build: Callable[[], M]) -> M:
    cache = cache_module.get_cache()
    hit = cache.get(key)
    if hit is not None:
        try:
            return model.model_validate_json(hit)
        except ValueError:
            pass  # a corrupt or stale-shaped entry is a miss
    value = build()
    cache.set(key, value.model_dump_json(), _ttl())
    return value


def _group_counts(session: SessionDep, column: InstrumentedAttribute[str]) -> dict[str, int]:
    return {
        row[0]: row[1] for row in session.execute(select(column, func.count()).group_by(column))
    }


@router.get("/summary", response_model=DashboardSummary)
def summary(
    session: SessionDep,
    now: NowDep,
    as_of: Annotated[
        date | None, Query(description="Last day of the 7-day trend; default today (UTC).")
    ] = None,
) -> DashboardSummary:
    day = as_of or now.astimezone(UTC).date()

    def build() -> DashboardSummary:
        total = queries.count_where(session, NormalisedTransaction)
        matched = queries.count_where(
            session, NormalisedTransaction, NormalisedTransaction.match_status == "MATCHED"
        )
        by_category = _group_counts(session, ReconException.category)
        open_exceptions = ReconException.status.in_(queries.OPEN_STATUSES)
        rate = round(matched / total, 4) if total else 0.0
        return DashboardSummary(
            as_of=day,
            total_transactions=total,
            matched_count=matched,
            match_rate=rate,
            match_rate_of_total=rate,
            avg_processing_time_seconds=queries.average_processing_seconds(session),
            daily_trend=[
                DailyTrendPoint(
                    day=p.day,
                    total_transactions=p.total_transactions,
                    matched_count=p.matched_count,
                    match_rate=p.match_rate,
                    exception_count=p.exception_count,
                )
                for p in queries.daily_trend(session, day)
            ],
            pending_review_count=queries.pending_review_count(session),
            exception_count=sum(by_category.values()),
            unresolved_count=queries.count_where(session, ReconException, open_exceptions),
            open_alert_count=queries.count_where(session, Alert, Alert.status == "OPEN"),
            sla_breached_open_count=queries.count_where(
                session,
                ReconException,
                open_exceptions,
                ReconException.sla_breached_at.is_not(None),
            ),
            exceptions_by_category=by_category,
            exceptions_by_severity=_group_counts(session, ReconException.severity),
        )

    return _cached(f"dashboard:summary:{day.isoformat()}", DashboardSummary, build)


@router.get("/banks", response_model=BanksDashboard)
def banks(
    session: SessionDep,
    now: NowDep,
    settings: Annotated[RunSettings, Depends(get_run_settings)],
) -> BanksDashboard:
    def build() -> BanksDashboard:
        rows = []
        for s in queries.bank_breakdown(session, sorted(settings.banks), now):
            config = settings.banks.get(s.bank_code)
            rows.append(
                BankDashboardRow(
                    bank_code=s.bank_code,
                    bank_name=config.bank_name if config else None,
                    total_transactions=s.total_transactions,
                    matched_count=s.matched_count,
                    match_rate=s.match_rate,
                    exception_count=s.exception_count,
                    open_exception_count=s.open_exception_count,
                    exception_rate=s.exception_rate,
                    avg_settlement_days=s.avg_settlement_days,
                    sla_compliance_percent=s.sla_compliance_percent,
                )
            )
        return BanksDashboard(banks=rows)

    return _cached("dashboard:banks", BanksDashboard, build)


@router.get("/timeline", response_model=TimelineDashboard)
def timeline(
    session: SessionDep,
    now: NowDep,
    day: Annotated[date | None, Query(alias="date", description="UTC day; default today.")] = None,
) -> TimelineDashboard:
    target = day or now.astimezone(UTC).date()

    def build() -> TimelineDashboard:
        hours = queries.hourly_timeline(session, target)
        return TimelineDashboard(
            date=target,
            total_transactions=sum(h.transaction_count for h in hours),
            hours=[
                TimelineHour(
                    hour=h.hour,
                    transaction_count=h.transaction_count,
                    matched_count=h.matched_count,
                    match_rate=h.match_rate,
                )
                for h in hours
            ],
        )

    return _cached(f"dashboard:timeline:{target.isoformat()}", TimelineDashboard, build)
