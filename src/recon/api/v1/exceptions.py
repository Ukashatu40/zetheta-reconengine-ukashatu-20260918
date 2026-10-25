# src/recon/api/v1/exceptions.py
"""Exception endpoints. Resolve authority is tier-based (DD-14): ANALYST
may resolve tiers 1-2; tiers 3-4 and every write-off need ADMIN."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import ColumnElement, Date, cast, func, select

from recon.api.auth import Principal, require_admin, require_analyst, require_viewer
from recon.api.deps import NowDep, SessionDep
from recon.api.v1.schemas.responses import (
    AssignRequest,
    ExceptionDetail,
    ExceptionEventOut,
    ExceptionOut,
    OverrideRequest,
    Page,
    ReopenRequest,
    ResolveRequest,
    SlaScanOut,
)
from recon.audit.chains import EXCEPTIONS_CHAIN
from recon.audit.logger import AuditLogger
from recon.domain.enums import (
    AuditActionType,
    ExceptionCategory,
    ExceptionSeverity,
    ExceptionStatus,
)
from recon.excmgmt.queues import is_queue, queue_for_tier
from recon.excmgmt.sla import SlaScanner
from recon.persistence.models import ExceptionEvent, ReconException
from recon.security.rbac import Role, has_at_least

router = APIRouter(prefix="/api/v1/exceptions", tags=["exceptions"])

_RESOLVABLE_STATUSES = ("OPEN", "IN_REVIEW", "ESCALATED", "REOPENED")
_EXPORT_LIMIT = 50_000
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: object) -> str:
    """A CSV cell, neutralised if it would be read as a spreadsheet formula."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


_SENIOR_TIER = 3


@router.get("", response_model=Page[ExceptionOut], dependencies=[Depends(require_viewer)])
def list_exceptions(
    session: SessionDep,
    *,
    status: ExceptionStatus | None = None,
    severity: ExceptionSeverity | None = None,
    category: ExceptionCategory | None = None,
    bank_code: Annotated[str | None, Query(max_length=20)] = None,
    tier: Annotated[int | None, Query(ge=1, le=4)] = None,
    assigned_to: Annotated[str | None, Query(max_length=100)] = None,
    date_from: Annotated[date | None, Query(description="Created on or after.")] = None,
    date_to: Annotated[date | None, Query(description="Created on or before.")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ExceptionOut]:
    filters: list[ColumnElement[bool]] = []
    created_day = cast(ReconException.created_at, Date)
    if tier is not None:
        filters.append(ReconException.assigned_tier == tier)
    if assigned_to is not None:
        filters.append(ReconException.assigned_to == assigned_to)
    if date_from is not None:
        filters.append(created_day >= date_from)
    if date_to is not None:
        filters.append(created_day <= date_to)
    if status is not None:
        filters.append(ReconException.status == status.value)
    if severity is not None:
        filters.append(ReconException.severity == severity.value)
    if category is not None:
        filters.append(ReconException.category == category.value)
    if bank_code is not None:
        filters.append(ReconException.bank_code == bank_code)

    total = session.scalar(select(func.count()).select_from(ReconException).where(*filters)) or 0
    rows = session.scalars(
        select(ReconException)
        .where(*filters)
        .order_by(ReconException.sla_deadline, ReconException.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[ExceptionOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/export", dependencies=[Depends(require_viewer)])
def export_exceptions(
    session: SessionDep,
    *,
    status: ExceptionStatus | None = None,
    severity: ExceptionSeverity | None = None,
    category: ExceptionCategory | None = None,
    bank_code: Annotated[str | None, Query(max_length=20)] = None,
    tier: Annotated[int | None, Query(ge=1, le=4)] = None,
) -> Response:
    """The exception list as CSV (at most 50,000 rows), for audit tools and spreadsheets."""
    filters: list[ColumnElement[bool]] = []
    for column, value in (
        (ReconException.status, status.value if status else None),
        (ReconException.severity, severity.value if severity else None),
        (ReconException.category, category.value if category else None),
        (ReconException.bank_code, bank_code),
        (ReconException.assigned_tier, tier),
    ):
        if value is not None:
            filters.append(column == value)
    rows = session.scalars(
        select(ReconException)
        .where(*filters)
        .order_by(ReconException.sla_deadline, ReconException.id)
        .limit(_EXPORT_LIMIT)
    ).all()
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "id",
            "category",
            "severity",
            "status",
            "tier",
            "assigned_to",
            "bank_code",
            "run_id",
            "amount",
            "currency",
            "amount_inr_minor",
            "created_at",
            "sla_deadline",
            "sla_breached_at",
            "resolved_at",
            "resolved_by",
            "rationale",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                _safe(v)
                for v in (
                    r.id,
                    r.category,
                    r.severity,
                    r.status,
                    r.assigned_tier,
                    r.assigned_to,
                    r.bank_code,
                    r.run_id,
                    r.financial_impact_amount,
                    r.financial_impact_currency,
                    r.financial_impact_inr_minor,
                    r.created_at.isoformat(),
                    r.sla_deadline.isoformat(),
                    r.sla_breached_at.isoformat() if r.sla_breached_at else None,
                    r.resolved_at.isoformat() if r.resolved_at else None,
                    r.resolved_by,
                    r.rationale,
                )
            ]
        )
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="exceptions.csv"'},
    )


@router.get(
    "/{exception_id}", response_model=ExceptionDetail, dependencies=[Depends(require_viewer)]
)
def get_exception(exception_id: uuid.UUID, session: SessionDep) -> ExceptionDetail:
    row = session.get(ReconException, exception_id)
    if row is None:
        raise HTTPException(status_code=404, detail="exception not found")
    events = session.scalars(
        select(ExceptionEvent)
        .where(ExceptionEvent.exception_id == exception_id)
        .order_by(ExceptionEvent.occurred_at, ExceptionEvent.id)
    ).all()
    return ExceptionDetail(
        **ExceptionOut.model_validate(row).model_dump(),
        events=[ExceptionEventOut.model_validate(e) for e in events],
    )


def _role_needed(row: ReconException, outcome: str) -> Role:
    if outcome == "WRITTEN_OFF" or row.assigned_tier >= _SENIOR_TIER:
        return Role.ADMIN
    return Role.ANALYST


@router.put("/{exception_id}/resolve", response_model=ExceptionOut)
def resolve_exception(
    exception_id: uuid.UUID,
    body: ResolveRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_analyst)],
    now: NowDep,
) -> ExceptionOut:
    row = session.get(ReconException, exception_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="exception not found")
    if row.status not in _RESOLVABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"exception is already {row.status}")
    needed = _role_needed(row, body.outcome)
    if not has_at_least(principal.role, needed):
        raise HTTPException(
            status_code=403, detail=f"resolving this exception requires role {needed} or higher"
        )

    actor = f"api-key:{principal.name}"
    before = {"status": row.status, "tier": row.assigned_tier}
    row.status = body.outcome
    row.resolved_at = now
    row.resolved_by = principal.name
    row.resolution_details = body.resolution_details
    session.add(
        ExceptionEvent(
            exception_id=row.id,
            event_type="RESOLVED",
            from_tier=row.assigned_tier,
            to_tier=row.assigned_tier,
            actor=actor,
            detail=f"{body.outcome}: {body.resolution_details}",
            occurred_at=now,
        )
    )
    AuditLogger(session).append(
        chain_id=EXCEPTIONS_CHAIN,
        actor_type="USER",
        actor_id=actor,
        action_type=AuditActionType.RESOLVE,
        occurred_at=now,
        affected_records={"exception_id": str(row.id)},
        before_state=before,
        after_state={"status": row.status, "tier": row.assigned_tier},
        rationale=body.resolution_details,
    )
    session.flush()
    result = ExceptionOut.model_validate(row)
    session.commit()
    return result


@router.put("/{exception_id}/assign", response_model=ExceptionOut)
def assign_exception(
    exception_id: uuid.UUID,
    body: AssignRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_analyst)],
    now: NowDep,
) -> ExceptionOut:
    """Hands an open exception to a named person (or back to a tier queue)."""
    row = session.get(ReconException, exception_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="exception not found")
    if row.status not in _RESOLVABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"exception is already {row.status}")
    actor = f"api-key:{principal.name}"
    before = row.assigned_to
    row.assigned_to = body.assignee
    session.add(
        ExceptionEvent(
            exception_id=row.id,
            event_type="ASSIGNED",
            from_tier=row.assigned_tier,
            to_tier=row.assigned_tier,
            actor=actor,
            detail=f"assigned to {body.assignee}",
            occurred_at=now,
        )
    )
    AuditLogger(session).append(
        chain_id=EXCEPTIONS_CHAIN,
        actor_type="USER",
        actor_id=actor,
        action_type=AuditActionType.ASSIGN,
        occurred_at=now,
        affected_records={"exception_id": str(row.id)},
        before_state={"assigned_to": before},
        after_state={"assigned_to": body.assignee},
        rationale=body.note or "assigned",
    )
    session.flush()
    result = ExceptionOut.model_validate(row)
    session.commit()
    return result


@router.post("/sla-scan", response_model=SlaScanOut)
def run_sla_scan(
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_admin)],
    now: NowDep,
) -> SlaScanOut:
    """Escalates every open exception whose SLA has passed (R66). Also run after
    each reconciliation; this endpoint lets an operator or a scheduler do it on demand."""
    outcome = SlaScanner(session, AuditLogger(session)).scan(now)
    session.commit()
    return SlaScanOut(
        escalated_count=outcome.escalated_count,
        breached_at_top_tier_count=outcome.breached_at_top_tier_count,
    )


@router.put("/{exception_id}/override", response_model=ExceptionOut)
def override_exception(
    exception_id: uuid.UUID,
    body: OverrideRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_admin)],
    now: NowDep,
) -> ExceptionOut:
    """ADMIN overrides the routing the rules chose (A9.2): moves an open exception to any tier."""
    row = session.get(ReconException, exception_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="exception not found")
    if row.status not in _RESOLVABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"exception is already {row.status}")
    actor = f"api-key:{principal.name}"
    before = {"tier": row.assigned_tier, "assigned_to": row.assigned_to}
    previous_tier = row.assigned_tier
    row.assigned_tier = body.tier
    if is_queue(row.assigned_to):  # a named person keeps the item
        row.assigned_to = queue_for_tier(body.tier)
    session.add(
        ExceptionEvent(
            exception_id=row.id,
            event_type="OVERRIDDEN",
            from_tier=previous_tier,
            to_tier=body.tier,
            actor=actor,
            detail=f"tier overridden: {body.reason}",
            occurred_at=now,
        )
    )
    AuditLogger(session).append(
        chain_id=EXCEPTIONS_CHAIN,
        actor_type="USER",
        actor_id=actor,
        action_type=AuditActionType.OVERRIDE,
        occurred_at=now,
        affected_records={"exception_id": str(row.id)},
        before_state=before,
        after_state={"tier": row.assigned_tier, "assigned_to": row.assigned_to},
        rationale=body.reason,
    )
    session.flush()
    result = ExceptionOut.model_validate(row)
    session.commit()
    return result


@router.post("/{exception_id}/reopen", response_model=ExceptionOut)
def reopen_exception(
    exception_id: uuid.UUID,
    body: ReopenRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_analyst)],
    now: NowDep,
) -> ExceptionOut:
    """Reopens a resolved exception (including one the auto-resolver closed) for a person
    to look at. It returns to at least Tier 2: it was wrong to treat it as automatic."""
    row = session.get(ReconException, exception_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="exception not found")
    if row.status not in ("RESOLVED", "WRITTEN_OFF"):
        raise HTTPException(status_code=409, detail=f"exception is {row.status}, not resolved")
    actor = f"api-key:{principal.name}"
    before = {"status": row.status, "tier": row.assigned_tier, "resolved_by": row.resolved_by}
    previous_tier = row.assigned_tier
    row.status = "REOPENED"
    row.assigned_tier = max(row.assigned_tier, 2)
    row.resolved_at = None
    row.resolved_by = None
    row.resolution_details = None
    row.assigned_to = queue_for_tier(row.assigned_tier)
    session.add(
        ExceptionEvent(
            exception_id=row.id,
            event_type="REOPENED",
            from_tier=previous_tier,
            to_tier=row.assigned_tier,
            actor=actor,
            detail=f"reopened: {body.reason}",
            occurred_at=now,
        )
    )
    AuditLogger(session).append(
        chain_id=EXCEPTIONS_CHAIN,
        actor_type="USER",
        actor_id=actor,
        action_type=AuditActionType.OVERRIDE,
        occurred_at=now,
        affected_records={"exception_id": str(row.id)},
        before_state=before,
        after_state={"status": "REOPENED", "tier": row.assigned_tier},
        rationale=body.reason,
    )
    session.flush()
    result = ExceptionOut.model_validate(row)
    session.commit()
    return result
