# src/recon/api/v1/dashboard.py
"""Dashboard summary. Partial against the PDF's field list: avg_processing_time
and daily_trend need the reconciliation_runs table, and no Redis cache yet."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from recon.api.auth import require_viewer
from recon.api.deps import SessionDep
from recon.api.v1.schemas.responses import DashboardSummary
from recon.persistence.models import MatchResult, NormalisedTransaction, ReconException

router = APIRouter(
    prefix="/api/v1/dashboard", tags=["dashboard"], dependencies=[Depends(require_viewer)]
)

_OPEN_STATUSES = ("OPEN", "IN_REVIEW", "ESCALATED", "REOPENED")


def _count(session: Session, entity: type, *conditions: ColumnElement[bool]) -> int:
    return session.scalar(select(func.count()).select_from(entity).where(*conditions)) or 0


@router.get("/summary", response_model=DashboardSummary)
def summary(session: SessionDep) -> DashboardSummary:
    total = _count(session, NormalisedTransaction)
    matched = _count(
        session, NormalisedTransaction, NormalisedTransaction.match_status == "MATCHED"
    )
    by_category = {
        row[0]: row[1]
        for row in session.execute(
            select(ReconException.category, func.count()).group_by(ReconException.category)
        )
    }
    by_severity = {
        row[0]: row[1]
        for row in session.execute(
            select(ReconException.severity, func.count()).group_by(ReconException.severity)
        )
    }
    return DashboardSummary(
        total_transactions=total,
        matched_count=matched,
        match_rate_of_total=round(matched / total, 4) if total else 0.0,
        pending_review_count=_count(session, MatchResult, MatchResult.status == "PENDING_REVIEW"),
        exception_count=sum(by_category.values()),
        unresolved_count=_count(session, ReconException, ReconException.status.in_(_OPEN_STATUSES)),
        sla_breached_open_count=_count(
            session,
            ReconException,
            ReconException.status.in_(_OPEN_STATUSES),
            ReconException.sla_breached_at.is_not(None),
        ),
        exceptions_by_category=by_category,
        exceptions_by_severity=by_severity,
    )
