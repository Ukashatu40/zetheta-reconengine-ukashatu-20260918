# src/recon/api/v1/runs.py
"""Reconciliation run endpoints. POST runs synchronously (DD-16)."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Response
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from recon.api.auth import Principal, require_analyst, require_viewer
from recon.api.deps import ClockDep, NowDep, SessionDep
from recon.api.v1.schemas.responses import (
    BacklogBankOut,
    BacklogDayOut,
    BacklogOut,
    BacklogRequest,
    MatchResultOut,
    Page,
    ReconcileRequest,
    RunOut,
)
from recon.persistence.models import MatchResult, ReconciliationRun
from recon.persistence.session import get_engine, get_session_factory
from recon.reporting.generator import ReportGenerator
from recon.runs.backlog import BacklogService, BankBacklogResult, ParallelMap, SessionFactory
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


@router.post("/reconciliation/run", response_model=RunOut, status_code=201)
@router.post("/reconcile", response_model=RunOut, status_code=201, deprecated=True)
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
            bank_code=body.bank_code,
            idempotency_key=idempotency_key,
            requested_by=principal.name,
            mode=body.mode,
            date_from=body.date_from,
            date_to=body.date_to,
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


@router.get(
    "/reconciliation/runs", response_model=Page[RunOut], dependencies=[Depends(require_viewer)]
)
@router.get(
    "/runs", response_model=Page[RunOut], dependencies=[Depends(require_viewer)], deprecated=True
)
def list_runs(
    session: SessionDep,
    bank_code: Annotated[str | None, Query(max_length=20)] = None,
    status: Annotated[str | None, Query(pattern="^(PENDING|RUNNING|COMPLETED|FAILED)$")] = None,
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
    "/reconciliation/runs/{run_id}/results",
    response_model=Page[MatchResultOut],
    dependencies=[Depends(require_viewer)],
)
@router.get(
    "/runs/{run_id}/results",
    response_model=Page[MatchResultOut],
    dependencies=[Depends(require_viewer)],
    deprecated=True,
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


_MAX_PARALLEL_BANKS = 8


def get_backlog_session_factory() -> SessionFactory:
    factory = get_session_factory(get_engine())

    @contextmanager
    def new_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    return new_session


def get_parallel_map() -> ParallelMap:
    def parallel(
        work: Callable[[str], BankBacklogResult], banks: list[str]
    ) -> Iterable[BankBacklogResult]:
        with ThreadPoolExecutor(max_workers=min(len(banks), _MAX_PARALLEL_BANKS) or 1) as pool:
            return list(pool.map(work, banks))

    return parallel


@router.post("/reconciliation/backlog", response_model=BacklogOut, status_code=201)
def recover_backlog(
    body: BacklogRequest,
    idempotency_key: IdempotencyKey,
    session: SessionDep,
    *,
    settings: Annotated[RunSettings, Depends(get_run_settings)],
    clock: ClockDep,
    now: NowDep,
    principal: Annotated[Principal, Depends(require_analyst)],
    factory: Annotated[SessionFactory, Depends(get_backlog_session_factory)],
    parallel: Annotated[ParallelMap, Depends(get_parallel_map)],
    background: BackgroundTasks,
) -> BacklogOut:
    """B4.3: newest day first, banks in parallel, exact matching only for the first pass,
    then the full pipeline queued as a background job. Returns the preliminary recovery
    figures for the CFO's report."""
    banks = sorted(body.banks if body.banks is not None else settings.banks)
    unknown = [b for b in banks if b not in settings.banks]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown bank_code {unknown[0]!r}")
    service = BacklogService(factory, settings, clock, parallel)
    results = service.run(
        batch_key=hashlib.sha256(idempotency_key.encode()).hexdigest()[:20],
        banks=banks,
        requested_by=principal.name,
        date_from=body.date_from,
        date_to=body.date_to,
        queue_full=body.queue_full,
    )
    queued = [r.queued_full_run_id for r in results if r.queued_full_run_id is not None]
    for run_id in queued:
        background.add_task(service.run_queued, run_id)

    recovery = ReportGenerator(session, settings.banks, now).recovery()
    return BacklogOut(
        banks=[
            BacklogBankOut(
                bank_code=r.bank_code,
                days_processed=len(r.days),
                exact_matched=r.exact_matched,
                failed_days=r.failed_days,
                days=[
                    BacklogDayOut(
                        day=d.day, run_id=d.run_id, status=d.status, exact_matched=d.exact_matched
                    )
                    for d in r.days
                ],
                queued_full_run_id=r.queued_full_run_id,
            )
            for r in results
        ],
        queued_full_runs=len(queued),
        recovery=dict(recovery.summary),
    )
