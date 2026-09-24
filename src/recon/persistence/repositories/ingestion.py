# src/recon/persistence/repositories/ingestion.py
"""Repository for ingestion_files and raw_transactions.

Thin wrapper over SQLAlchemy Core/ORM operations — no business logic here.
Business logic (hash-based duplicate rejection, row-by-row error handling)
lives in recon.ingestion.service.IngestionService, which is the only
caller of this repository in normal operation.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from recon.persistence.models import IngestionFile, RawTransaction


class DuplicateFileError(ValueError):
    """Raised when a file with the same content_sha256 has already been
    ingested. The unique constraint on content_sha256 (see WP1's core-
    persistence migration) is the database-level backstop; this exception
    is the application-level check that runs before ever attempting an
    insert, so a duplicate is rejected without generating a failed
    transaction and without touching raw_transactions at all."""

    def __init__(self, content_sha256: str, existing_file_id: uuid.UUID) -> None:
        self.content_sha256 = content_sha256
        self.existing_file_id = existing_file_id
        super().__init__(
            f"file with content_sha256={content_sha256} was already ingested "
            f"as ingestion_files.id={existing_file_id}"
        )


class IngestionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find_by_content_hash(self, content_sha256: str) -> IngestionFile | None:
        return (
            self._session.query(IngestionFile)
            .filter(IngestionFile.content_sha256 == content_sha256)
            .one_or_none()
        )

    def create_ingestion_file(
        self,
        *,
        bank_code: str,
        format_type: str,
        original_filename: str,
        content_sha256: str,
        size_bytes: int,
        ingested_by: str,
    ) -> IngestionFile:
        existing = self.find_by_content_hash(content_sha256)
        if existing is not None:
            raise DuplicateFileError(content_sha256, existing.id)

        ingestion_file = IngestionFile(
            bank_code=bank_code,
            format_type=format_type,
            original_filename=original_filename,
            content_sha256=content_sha256,
            size_bytes=size_bytes,
            ingested_by=ingested_by,
        )
        self._session.add(ingestion_file)
        self._session.flush()  # populate ingestion_file.id without committing
        return ingestion_file

    def add_raw_transaction(
        self,
        *,
        ingestion_file_id: uuid.UUID,
        ingested_on: date,
        bank_code: str,
        format_type: str,
        source_line_no: int | None,
        raw_payload: dict[str, Any],
        parse_status: str,
        parse_errors: dict[str, Any] | None,
    ) -> RawTransaction:
        row = RawTransaction(
            ingestion_file_id=ingestion_file_id,
            ingested_on=ingested_on,
            bank_code=bank_code,
            format_type=format_type,
            source_line_no=source_line_no,
            raw_payload=raw_payload,
            parse_status=parse_status,
            parse_errors=parse_errors,
        )
        self._session.add(row)
        return row

    def update_file_status(
        self,
        ingestion_file: IngestionFile,
        *,
        status: str,
        record_count: int,
        parse_error_count: int,
    ) -> None:
        ingestion_file.status = status
        ingestion_file.record_count = record_count
        ingestion_file.parse_error_count = parse_error_count
        self._session.flush()
