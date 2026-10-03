# src/recon/api/v1/exceptions.py
"""Exception endpoints. Resolve authority is tier-based (DD-14): ANALYST
may resolve tiers 1-2; tiers 3-4 and every write-off need ADMIN."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import ColumnElement, func, select

from recon.api.auth import Principal, require_analyst, require_viewer
from recon.api.deps import NowDep, SessionDep
from recon.api.v1.schemas.responses import (
    ExceptionDetail,
    ExceptionEventOut,
    ExceptionOut,
    Page,
    ResolveRequest,
)
from recon.audit.chains import EXCEPTIONS_CHAIN
from recon.audit.logger import AuditLogger
from recon.domain.enums import (
    AuditActionType,
    ExceptionCategory,
    ExceptionSeverity,
    ExceptionStatus,
)
from recon.persistence.models import ExceptionEvent, ReconException
from recon.security.rbac import Role, has_at_least

router = APIRouter(prefix="/api/v1/exceptions", tags=["exceptions"])

_RESOLVABLE_STATUSES = ("OPEN", "IN_REVIEW", "ESCALATED", "REOPENED")
_SENIOR_TIER = 3


@router.get("", response_model=Page[ExceptionOut], dependencies=[Depends(require_viewer)])
def list_exceptions(
    session: SessionDep,
    *,
    status: ExceptionStatus | None = None,
    severity: ExceptionSeverity | None = None,
    category: ExceptionCategory | None = None,
    bank_code: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ExceptionOut]:
    filters: list[ColumnElement[bool]] = []
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
