# src/recon/api/v1/runs.py
"""Reconciliation run endpoints. POST runs synchronously (DD-16)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import ColumnElement, func, select

from recon.api.auth import Principal, require_analyst, require_viewer
from recon.api.deps import ClockDep, SessionDep
from recon.api.v1.schemas.responses import MatchResultOut, Page, ReconcileRequest, RunOut
from recon.persistence.models import MatchResult, ReconciliationRun
from recon.runs.service import (
    IdempotencyConflictError,
    RunAlreadyActiveError,
    RunService,
)
from recon.runs.settings import RunSettings, default_settings

router = APIRouter(prefix="/api/v1", tags=["runs"])

IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$"),
]


def get_run_settings() -> RunSettings:
    return default_settings()


def get_run_service(
    session: SessionDep,
    settings: Annotated[RunSettings, Depends(get_run_settings)],
    clock: ClockDep,
) -> RunService:
    return RunService(session, settings, clock)


RunServiceDep = Annotated[RunService, Depends(get_run_service)]


@router.post("/reconcile", response_model=RunOut, status_code=201)
def start_reconciliation(
    body: ReconcileRequest,
    idempotency_key: IdempotencyKey,
    session: SessionDep,
    *,
    settings: Annotated[RunSettings, Depends(get_run_settings)],
    service: RunServiceDep,
    principal: Annotated[Principal, Depends(require_analyst)],
    response: Response,
) -> RunOut:
    if body.bank_code not in settings.banks:
        raise HTTPException(status_code=422, detail=f"unknown bank_code {body.bank_code!r}")
    try:
        started = service.start(
            bank_code=body.bank_code, idempotency_key=idempotency_key, requested_by=principal.name
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()  # the RUNNING row must be visible to other connections before work starts

    if started.created:
        service.execute(started.run.id)
        session.commit()
    else:
        response.status_code = 200  # replay of an earlier request

    run = session.get(ReconciliationRun, started.run.id)
    assert run is not None
    return RunOut.model_validate(run)


@router.get("/runs", response_model=Page[RunOut], dependencies=[Depends(require_viewer)])
def list_runs(
    session: SessionDep,
    bank_code: Annotated[str | None, Query(max_length=20)] = None,
    status: Annotated[str | None, Query(pattern="^(RUNNING|COMPLETED|FAILED)$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[RunOut]:
    filters: list[ColumnElement[bool]] = []
    if bank_code is not None:
        filters.append(ReconciliationRun.bank_code == bank_code)
    if status is not None:
        filters.append(ReconciliationRun.status == status)
    total = session.scalar(select(func.count()).select_from(ReconciliationRun).where(*filters)) or 0
    rows = session.scalars(
        select(ReconciliationRun)
        .where(*filters)
        .order_by(ReconciliationRun.created_at.desc(), ReconciliationRun.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[RunOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get(
    "/runs/{run_id}/results",
    response_model=Page[MatchResultOut],
    dependencies=[Depends(require_viewer)],
)
def run_results(
    run_id: uuid.UUID,
    session: SessionDep,
    *,
    status: Annotated[
        str | None, Query(pattern="^(AUTO_MATCHED|PENDING_REVIEW|CONFIRMED|REJECTED)$")
    ] = None,
    match_type: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[MatchResultOut]:
    if session.get(ReconciliationRun, run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    filters: list[ColumnElement[bool]] = [MatchResult.run_id == str(run_id)]
    if status is not None:
        filters.append(MatchResult.status == status)
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
