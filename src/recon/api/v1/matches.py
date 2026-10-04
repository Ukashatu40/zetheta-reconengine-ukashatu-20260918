# src/recon/api/v1/matches.py
"""Match review endpoints. ANALYST or higher may review (no four-eyes rule)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from recon.api.auth import Principal, require_analyst, require_viewer
from recon.api.deps import NowDep, SessionDep
from recon.api.v1.schemas.responses import ConfirmRequest, MatchResultOut, Page, RejectRequest
from recon.audit.logger import AuditLogger
from recon.matching.review import (
    MatchNotFoundError,
    MatchNotPendingError,
    MatchReviewService,
    ReviewStateError,
)
from recon.persistence.models import MatchResult

router = APIRouter(prefix="/api/v1/matches", tags=["matches"])


@router.get("", response_model=Page[MatchResultOut], dependencies=[Depends(require_viewer)])
def list_matches(
    session: SessionDep,
    status: Annotated[
        str, Query(pattern="^(AUTO_MATCHED|PENDING_REVIEW|CONFIRMED|REJECTED)$")
    ] = "PENDING_REVIEW",
    match_type: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[MatchResultOut]:
    filters: list[ColumnElement[bool]] = [MatchResult.status == status]
    if match_type is not None:
        filters.append(MatchResult.match_type == match_type)
    total = session.scalar(select(func.count()).select_from(MatchResult).where(*filters)) or 0
    rows = session.scalars(
        select(MatchResult)
        .where(*filters)
        .order_by(MatchResult.created_at, MatchResult.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[MatchResultOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _review(session: Session, action: Callable[[], MatchResult]) -> MatchResultOut:
    try:
        result = action()
    except MatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail="match not found") from exc
    except (MatchNotPendingError, ReviewStateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    out = MatchResultOut.model_validate(result)
    session.commit()
    return out


@router.put("/{match_id}/confirm", response_model=MatchResultOut)
def confirm_match(
    match_id: uuid.UUID,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_analyst)],
    now: NowDep,
    body: ConfirmRequest | None = None,
) -> MatchResultOut:
    service = MatchReviewService(session, AuditLogger(session))
    note = body.note if body else None
    return _review(
        session, lambda: service.confirm(match_id, reviewer=principal.name, now=now, note=note)
    )


@router.put("/{match_id}/reject", response_model=MatchResultOut)
def reject_match(
    match_id: uuid.UUID,
    body: RejectRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_analyst)],
    now: NowDep,
) -> MatchResultOut:
    service = MatchReviewService(session, AuditLogger(session))
    return _review(
        session,
        lambda: service.reject(match_id, reviewer=principal.name, now=now, reason=body.reason),
    )
