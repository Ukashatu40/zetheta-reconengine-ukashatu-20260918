# src/recon/ingestion/service.py
"""IngestionService: orchestrates hashing, duplicate rejection, parsing and
persistence for a single source file.

This is the first piece of code where recon.ingestion (infrastructure-
adjacent) and recon.persistence meet — recon.domain and recon.ingestion's
parser/config layers stay pure and untested-against-a-database, as before;
this module is where a real Session is required, which is why its tests
live under tests/integration/, not tests/unit/.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from recon.config.models import BankConfig
from recon.ingestion.integrity import compute_sha256, file_size_bytes
from recon.ingestion.parsed_row import ParsedRow
from recon.ingestion.parsers.csv_parser import CSVParser, open_stream
from recon.persistence.repositories.ingestion import DuplicateFileError, IngestionRepository


@dataclass(frozen=True, slots=True)
class IngestionResult:
    ingestion_file_id: str
    record_count: int
    parsed_count: int
    error_count: int
    status: str


class IngestionService:
    """Currently wired for CSV only. MT940Parser and CAMT053Parser
    (WP2 Increments 3-4) will extend the format_type -> parser dispatch
    below rather than requiring a new service."""

    def __init__(self, session: Session, ingested_by: str) -> None:
        self._session = session
        self._repository = IngestionRepository(session)
        self._ingested_by = ingested_by

    def ingest_csv_file(self, path: Path, config: BankConfig) -> IngestionResult:
        content_sha256 = compute_sha256(path)

        try:
            ingestion_file = self._repository.create_ingestion_file(
                bank_code=config.bank_code,
                format_type="CSV",
                original_filename=path.name,
                content_sha256=content_sha256,
                size_bytes=file_size_bytes(path),
                ingested_by=self._ingested_by,
            )
        except DuplicateFileError:
            raise

        if config.csv is None:
            raise ValueError(f"{config.bank_code}: no CSV configuration present")

        ingested_on = datetime.now(UTC).date()
        parser = CSVParser()
        error_count = 0
        parsed_count = 0

        with open_stream(path, config.csv) as stream:
            for row in parser.parse(stream, config):
                parse_status = "PARSED" if row.is_valid else "QUARANTINED"
                if not row.is_valid:
                    error_count += 1
                else:
                    parsed_count += 1

                self._repository.add_raw_transaction(
                    ingestion_file_id=ingestion_file.id,
                    ingested_on=ingested_on,
                    bank_code=config.bank_code,
                    format_type="CSV",
                    source_line_no=row.line_no,
                    raw_payload=self._row_to_payload(row),
                    parse_status=parse_status,
                    parse_errors=self._errors_to_payload(row) if not row.is_valid else None,
                )

        total = parsed_count + error_count
        file_status = (
            "PARSED" if error_count == 0 else "PARTIALLY_PARSED" if parsed_count else "FAILED"
        )

        self._repository.update_file_status(
            ingestion_file,
            status=file_status,
            record_count=total,
            parse_error_count=error_count,
        )

        return IngestionResult(
            ingestion_file_id=str(ingestion_file.id),
            record_count=total,
            parsed_count=parsed_count,
            error_count=error_count,
            status=file_status,
        )

    @staticmethod
    def _row_to_payload(row: ParsedRow) -> dict[str, object]:
        return {
            "raw_fields": row.raw_fields,
            "mapped": row.mapped,
        }

    @staticmethod
    def _errors_to_payload(row: ParsedRow) -> dict[str, object]:
        return {
            "errors": [
                {"code": str(e.code), "field": e.field, "detail": e.detail} for e in row.errors
            ]
        }
