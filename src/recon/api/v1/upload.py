# src/recon/api/v1/upload.py
"""File upload: stream, validate, then hand the saved file to the ingestion
pipeline (ingest, normalise, classify, scan, audit).

One transaction: any failure rolls everything back and deletes the saved
file. A ValueError is reported as 422 with a generic message (the detail is
logged); anything else propagates as a 500. A format change is the exception to
"nothing is kept": the file is rejected, but the alert that says so is saved."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recon.anomaly.service import AnomalyService
from recon.api.auth import Principal, require_analyst
from recon.api.deps import ClockDep, SessionDep
from recon.api.v1.runs import get_run_settings
from recon.api.v1.schemas.responses import UploadOut
from recon.config.models import BankConfig
from recon.config.registry import default_bank_configs
from recon.ingestion.format_check import FormatChangeDetectedError
from recon.ingestion.pipeline import IngestionPipeline
from recon.ingestion.upload import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    UploadRejectedError,
    extension_of,
    safe_display_name,
    save_stream,
)
from recon.persistence.models import IngestionFile
from recon.persistence.repositories.ingestion import DuplicateFileError
from recon.runs.service import RunAlreadyActiveError, RunService
from recon.runs.settings import RunSettings

logger = logging.getLogger("recon.upload")
router = APIRouter(prefix="/api/v1", tags=["upload"])


def get_upload_dir() -> Path:
    return Path(os.environ.get("RECON_UPLOAD_DIR", "/var/lib/recon/uploads"))


def get_bank_configs() -> dict[str, BankConfig]:
    return default_bank_configs()


def _discard(session: Session, path: Path) -> None:
    session.rollback()
    path.unlink(missing_ok=True)


def _reconcile_now(
    session: Session,
    settings: RunSettings,
    clock: Callable[[], datetime],
    *,
    principal: Principal,
    bank_code: str,
    file_id: uuid.UUID,
) -> uuid.UUID | None:
    """Near-real-time reconciliation (Case Study C1): exact matching for the bank as soon
    as a file lands, instead of waiting for a daily batch. Skipped, not failed, if the bank
    already has a run in progress."""
    service = RunService(session, settings, clock)
    try:
        started = service.start(
            bank_code=bank_code,
            idempotency_key=f"ingest:{file_id}",
            requested_by=principal.name,
            mode="FAST",
        )
        session.commit()
    except RunAlreadyActiveError:
        session.rollback()
        return None
    if started.created:
        service.execute(started.run.id)
        session.commit()
    return started.run.id


def _validate_target(
    banks: dict[str, BankConfig], bank_code: str, format_type: str, filename: str | None
) -> tuple[BankConfig, str]:
    config = banks.get(bank_code)
    if config is None:
        raise HTTPException(status_code=422, detail=f"unknown bank_code {bank_code!r}")
    if format_type not in config.supported_formats:
        raise HTTPException(status_code=422, detail=f"{bank_code} does not support {format_type}")
    extension = extension_of(filename or "")
    if extension not in ALLOWED_EXTENSIONS[format_type]:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS[format_type]))
        raise HTTPException(
            status_code=422, detail=f"{format_type} files must end in one of: {allowed}"
        )
    return config, extension


@router.post("/files/upload", response_model=UploadOut, status_code=201)
@router.post("/upload", response_model=UploadOut, status_code=201, deprecated=True)
def upload_file(
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_analyst)],
    clock: ClockDep,
    upload_dir: Annotated[Path, Depends(get_upload_dir)],
    banks: Annotated[dict[str, BankConfig], Depends(get_bank_configs)],
    *,
    run_settings: Annotated[RunSettings, Depends(get_run_settings)],
    file: Annotated[UploadFile, File()],
    bank_code: Annotated[str, Form(pattern=r"^[A-Z0-9_]{2,20}$")],
    source: Annotated[Literal["INTERNAL", "EXTERNAL"], Form()],
    format_type: Annotated[Literal["CSV", "MT940", "CAMT053"], Form()],
    auto_reconcile: Annotated[
        bool, Form(description="Run exact matching for this bank right after ingestion.")
    ] = False,
) -> UploadOut:
    config, extension = _validate_target(banks, bank_code, format_type, file.filename)
    try:
        saved = save_stream(file.file, upload_dir, extension, MAX_UPLOAD_BYTES)
    except UploadRejectedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    if (
        session.scalar(select(IngestionFile.id).where(IngestionFile.content_sha256 == saved.sha256))
        is not None
    ):
        saved.path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=409, detail="a file with identical content was already ingested"
        )

    display_name = safe_display_name(file.filename)
    try:
        outcome = IngestionPipeline(session, run_settings, f"api-key:{principal.name}").process(
            path=saved.path,
            display_name=display_name,
            sha256=saved.sha256,
            size_bytes=saved.size_bytes,
            config=config,
            format_type=format_type,
            source=source,
            now=clock(),
        )
        session.commit()
    except IntegrityError as exc:
        _discard(session, saved.path)
        raise HTTPException(
            status_code=409,
            detail="upload conflicted with existing data (possibly a concurrent upload)",
        ) from exc
    except FormatChangeDetectedError as exc:
        _discard(session, saved.path)
        AnomalyService(session, run_settings.anomaly, run_settings.banks).format_change_alert(
            bank_code=bank_code, filename=display_name, change=exc.change, blocked=True, now=clock()
        )
        session.commit()  # the alert outlives the rejected upload
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateFileError as exc:
        _discard(session, saved.path)
        raise HTTPException(
            status_code=409, detail="a file with identical content was already ingested"
        ) from exc
    except ValueError as exc:
        _discard(session, saved.path)
        logger.info("upload could not be processed (%s)", type(exc).__name__, exc_info=True)
        raise HTTPException(
            status_code=422, detail="file could not be processed; check its format"
        ) from exc
    except Exception:
        _discard(session, saved.path)
        raise

    result = outcome.result
    run_id = (
        _reconcile_now(
            session,
            run_settings,
            clock,
            principal=principal,
            bank_code=bank_code,
            file_id=outcome.file_row.id,
        )
        if auto_reconcile
        else None
    )
    return UploadOut(
        ingestion_file_id=outcome.file_row.id,
        bank_code=bank_code,
        format_type=format_type,
        source=source,
        original_filename=display_name,
        size_bytes=saved.size_bytes,
        content_sha256=saved.sha256,
        status=result.status,
        record_count=result.record_count,
        parsed_count=result.parsed_count,
        error_count=result.error_count,
        normalised_count=outcome.normalised.normalised_count,
        normalisation_failed_count=outcome.normalised.failed_count,
        exceptions_created=outcome.classified.created_count,
        alerts_raised=outcome.alerts_raised,
        pan_masked_count=result.pan_masked_count,
        statement_balanced=(
            result.balance_check.balanced if result.balance_check is not None else None
        ),
        reconciliation_run_id=run_id,
    )
