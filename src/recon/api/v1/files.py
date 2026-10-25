# src/recon/api/v1/files.py
"""File Ingestion Monitor endpoints (Day 6): list ingested files with their hash,
counts and integrity status, and re-verify stored files against that hash."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import ColumnElement, func, select

from recon.anomaly.service import AnomalyService
from recon.api.auth import Principal, require_admin, require_analyst, require_viewer
from recon.api.deps import NowDep, SessionDep
from recon.api.v1.runs import get_run_settings
from recon.api.v1.schemas.responses import (
    FileOut,
    FileVerificationOut,
    Page,
    VerifyAllOut,
)
from recon.ingestion.verification import FileVerifier
from recon.persistence.models import IngestionFile
from recon.runs.settings import RunSettings

router = APIRouter(prefix="/api/v1/files", tags=["files"])


@router.get("", response_model=Page[FileOut], dependencies=[Depends(require_viewer)])
def list_files(
    session: SessionDep,
    *,
    bank_code: Annotated[str | None, Query(max_length=20)] = None,
    source: Annotated[str | None, Query(pattern="^(INTERNAL|EXTERNAL)$")] = None,
    format_type: Annotated[str | None, Query(pattern="^(CSV|MT940|CAMT053)$")] = None,
    status: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FileOut]:
    filters: list[ColumnElement[bool]] = []
    for column, value in (
        (IngestionFile.bank_code, bank_code),
        (IngestionFile.source, source),
        (IngestionFile.format_type, format_type),
        (IngestionFile.status, status),
    ):
        if value is not None:
            filters.append(column == value)
    total = session.scalar(select(func.count()).select_from(IngestionFile).where(*filters)) or 0
    rows = session.scalars(
        select(IngestionFile)
        .where(*filters)
        .order_by(IngestionFile.ingested_at.desc(), IngestionFile.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[FileOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{file_id}", response_model=FileOut, dependencies=[Depends(require_viewer)])
def get_file(file_id: uuid.UUID, session: SessionDep) -> FileOut:
    row = session.get(IngestionFile, file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="file not found")
    return FileOut.model_validate(row)


@router.post("/verify", response_model=VerifyAllOut)
def verify_all_files(
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_admin)],
    settings: Annotated[RunSettings, Depends(get_run_settings)],
    now: NowDep,
) -> VerifyAllOut:
    """Re-hashes every stored file; a modified or missing one raises FILE_TAMPERED."""
    verifier = FileVerifier(
        session,
        AnomalyService(session, settings.anomaly, settings.banks),
        f"api-key:{principal.name}",
    )
    outcome = verifier.verify_all(now)
    session.commit()
    return VerifyAllOut(
        checked=outcome.checked, ok=outcome.ok, tampered=outcome.tampered, missing=outcome.missing
    )


@router.post("/{file_id}/verify", response_model=FileVerificationOut)
def verify_file(
    file_id: uuid.UUID,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_analyst)],
    settings: Annotated[RunSettings, Depends(get_run_settings)],
    now: NowDep,
) -> FileVerificationOut:
    row = session.get(IngestionFile, file_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="file not found")
    verifier = FileVerifier(
        session,
        AnomalyService(session, settings.anomaly, settings.banks),
        f"api-key:{principal.name}",
    )
    try:
        result = verifier.verify(row, now)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return FileVerificationOut(
        ingestion_file_id=file_id,
        status=result.status,
        expected_sha256=result.expected_sha256,
        actual_sha256=result.actual_sha256,
    )
