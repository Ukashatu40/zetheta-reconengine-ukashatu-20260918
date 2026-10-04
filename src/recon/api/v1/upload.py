# src/recon/api/v1/upload.py
"""File upload: stream, validate, ingest, normalise, classify, audit.

One transaction: any failure rolls everything back and deletes the saved
file. A ValueError is reported as 422 with a generic message (the detail is
logged); anything else propagates as a 500."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recon.api.auth import Principal, require_analyst
from recon.api.deps import ClockDep, SessionDep
from recon.api.v1.runs import get_run_settings
from recon.api.v1.schemas.responses import UploadOut
from recon.audit.chains import INGESTION_CHAIN
from recon.audit.logger import AuditLogger
from recon.config.models import BankConfig
from recon.config.registry import default_bank_configs
from recon.domain.enums import AuditActionType, SourceFormat, TransactionSource
from recon.excmgmt.classifier import ExceptionClassifier
from recon.ingestion.normalisation_service import NormalisationService
from recon.ingestion.service import IngestionService
from recon.ingestion.upload import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    UploadRejectedError,
    extension_of,
    safe_display_name,
    save_stream,
)
from recon.persistence.models import IngestionFile
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


@router.post("/upload", response_model=UploadOut, status_code=201)
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
) -> UploadOut:
    config = banks.get(bank_code)
    if config is None:
        raise HTTPException(status_code=422, detail=f"unknown bank_code {bank_code!r}")
    if format_type not in config.supported_formats:
        raise HTTPException(status_code=422, detail=f"{bank_code} does not support {format_type}")
    extension = extension_of(file.filename or "")
    if extension not in ALLOWED_EXTENSIONS[format_type]:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS[format_type]))
        raise HTTPException(
            status_code=422, detail=f"{format_type} files must end in one of: {allowed}"
        )

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

    actor = f"api-key:{principal.name}"
    try:
        result = IngestionService(session, actor).ingest_file(saved.path, config, format_type)
        file_row = session.get(IngestionFile, uuid.UUID(result.ingestion_file_id))
        if file_row is None:
            raise ValueError("ingestion file row is missing after ingestion")
        display_name = safe_display_name(file.filename)
        file_row.original_filename = display_name
        normalised = NormalisationService(session).normalise_ingestion_file(
            result.ingestion_file_id, config, SourceFormat(format_type), TransactionSource(source)
        )
        now = clock()
        classified = ExceptionClassifier(
            session, run_settings.taxonomy, AuditLogger(session)
        ).classify_ingestion_file(file_row.id, now)
        AuditLogger(session).append(
            chain_id=INGESTION_CHAIN,
            actor_type="USER",
            actor_id=actor,
            action_type=AuditActionType.INGEST,
            occurred_at=now,
            affected_records={"ingestion_file_id": result.ingestion_file_id},
            after_state={
                "bank_code": bank_code,
                "format_type": format_type,
                "source": source,
                "size_bytes": saved.size_bytes,
                "content_sha256": saved.sha256,
                "record_count": result.record_count,
                "parsed_count": result.parsed_count,
                "error_count": result.error_count,
            },
            rationale="file uploaded and ingested",
        )
        session.commit()
    except IntegrityError as exc:
        _discard(session, saved.path)
        raise HTTPException(
            status_code=409,
            detail="upload conflicted with existing data (possibly a concurrent upload)",
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

    return UploadOut(
        ingestion_file_id=file_row.id,
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
        normalised_count=normalised.normalised_count,
        normalisation_failed_count=normalised.failed_count,
        exceptions_created=classified.created_count,
    )
