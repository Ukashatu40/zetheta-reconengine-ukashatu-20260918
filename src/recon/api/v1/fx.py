# src/recon/api/v1/fx.py
"""Exchange-rate endpoints (A5.1): upload a rates file, list stored rates."""

from __future__ import annotations

import io
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import ColumnElement, func, select

from recon.api.auth import Principal, require_analyst, require_viewer
from recon.api.deps import ClockDep, SessionDep
from recon.api.v1.schemas.responses import FxRateOut, FxUploadOut, Page
from recon.audit.chains import INGESTION_CHAIN
from recon.audit.logger import AuditLogger
from recon.domain.enums import AuditActionType
from recon.fx.loader import parse_fx_csv
from recon.persistence.models import FxRate
from recon.persistence.repositories.fx import FxRateRepository

router = APIRouter(prefix="/api/v1/fx-rates", tags=["fx"])

_MAX_FX_BYTES = 5 * 1024 * 1024
_ERROR_SAMPLE = 20


@router.post("/upload", response_model=FxUploadOut, status_code=201)
def upload_fx_rates(
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_analyst)],
    clock: ClockDep,
    file: Annotated[UploadFile, File()],
) -> FxUploadOut:
    raw = file.file.read(_MAX_FX_BYTES + 1)
    if len(raw) > _MAX_FX_BYTES:
        raise HTTPException(status_code=413, detail="fx file is too large")
    try:
        result = parse_fx_csv(io.StringIO(raw.decode("utf-8-sig")))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"fx file could not be read: {exc}") from exc
    if not result.rates:
        raise HTTPException(status_code=422, detail="fx file contains no usable rates")

    inserted, skipped = FxRateRepository(session).insert_many(result.rates)
    AuditLogger(session).append(
        chain_id=INGESTION_CHAIN,
        actor_type="USER",
        actor_id=f"api-key:{principal.name}",
        action_type=AuditActionType.INGEST,
        occurred_at=clock(),
        affected_records={"kind": "fx_rates"},
        after_state={
            "inserted": inserted,
            "skipped_existing": skipped,
            "rejected": len(result.errors),
            "suspect": len(result.suspects),
        },
        rationale="exchange-rate file loaded",
    )
    session.commit()
    return FxUploadOut(
        inserted=inserted,
        skipped_existing=skipped,
        rejected_count=len(result.errors),
        rejected_sample=[f"line {e.line_no}: {e.detail}" for e in result.errors[:_ERROR_SAMPLE]],
        suspect_count=len(result.suspects),
        suspect_sample=[
            f"line {r.line_no}: {r.currency} {r.rate_date} {r.rate_inr} ({r.suspect_reason})"
            for r in result.suspects[:_ERROR_SAMPLE]
        ],
    )


@router.get("", response_model=Page[FxRateOut], dependencies=[Depends(require_viewer)])
def list_fx_rates(
    session: SessionDep,
    *,
    currency: Annotated[str | None, Query(min_length=3, max_length=3)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    suspect: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FxRateOut]:
    filters: list[ColumnElement[bool]] = []
    if currency is not None:
        filters.append(FxRate.currency == currency.upper())
    if date_from is not None:
        filters.append(FxRate.rate_date >= date_from)
    if date_to is not None:
        filters.append(FxRate.rate_date <= date_to)
    if suspect is not None:
        filters.append(FxRate.suspect.is_(suspect))
    total = session.scalar(select(func.count()).select_from(FxRate).where(*filters)) or 0
    rows = session.scalars(
        select(FxRate)
        .where(*filters)
        .order_by(FxRate.rate_date.desc(), FxRate.currency, FxRate.source)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[FxRateOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )
