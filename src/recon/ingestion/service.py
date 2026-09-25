# src/recon/ingestion/service.py
"""IngestionService: orchestrates hashing, duplicate rejection, parsing and
persistence for a single source file, across all three supported formats.

This is the first piece of code where recon.ingestion (infrastructure-
adjacent) and recon.persistence meet — recon.domain and recon.ingestion's
parser/config layers stay pure and untested-against-a-database, as before;
this module is where a real Session is required, which is why its tests
live under tests/integration/, not tests/unit/.

CSVParser and MT940Parser open their source in TEXT mode (IO[str]),
decoding with the bank's configured encoding up front. CAMT053Parser
opens in BINARY mode (IO[bytes]) so lxml can honor the XML prolog's own
declared encoding rather than have it pre-decided by us (see
recon.ingestion.parsers.camt053.parser's module docstring). This is why
ingest_file dispatches to format-specific private methods rather than
sharing one generic "open and parse" code path — the two parser families
genuinely need different file-opening semantics, not just different
parser classes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from recon.config.models import BankConfig
from recon.ingestion.integrity import compute_sha256, file_size_bytes
from recon.ingestion.parsed_row import ParsedRow
from recon.ingestion.parsers.camt053.parser import CAMT053Parser
from recon.ingestion.parsers.csv_parser import CSVParser, open_stream
from recon.ingestion.parsers.mt940.state_machine import MT940Parser
from recon.persistence.models import IngestionFile
from recon.persistence.repositories.ingestion import IngestionRepository


@dataclass(frozen=True, slots=True)
class IngestionResult:
    ingestion_file_id: str
    record_count: int
    parsed_count: int
    error_count: int
    status: str


class UnsupportedFormatError(ValueError):
    """Raised when ingest_file is asked to handle a format_type this
    service has no dispatch entry for."""


class IngestionService:
    _SUPPORTED_FORMATS = ("CSV", "MT940", "CAMT053")

    def __init__(self, session: Session, ingested_by: str) -> None:
        self._session = session
        self._repository = IngestionRepository(session)
        self._ingested_by = ingested_by

    def ingest_file(self, path: Path, config: BankConfig, format_type: str) -> IngestionResult:
        """Dispatches to the correct parser and stream-opening mode for
        format_type. format_type is passed explicitly rather than
        inferred from config.supported_formats, since a bank config may
        legitimately support more than one format (see axis.v1.yaml,
        which supports both CSV and MT940) — the caller decides which
        format this particular file is."""
        if format_type not in self._SUPPORTED_FORMATS:
            raise UnsupportedFormatError(
                f"{format_type!r} is not supported; expected one of {self._SUPPORTED_FORMATS}"
            )
        if format_type not in config.supported_formats:
            raise UnsupportedFormatError(
                f"{config.bank_code}'s configuration does not list {format_type!r} "
                f"among its supported_formats ({config.supported_formats})"
            )

        content_sha256 = compute_sha256(path)
        ingestion_file = self._repository.create_ingestion_file(
            bank_code=config.bank_code,
            format_type=format_type,
            original_filename=path.name,
            content_sha256=content_sha256,
            size_bytes=file_size_bytes(path),
            ingested_by=self._ingested_by,
        )

        rows = list(self._parse_rows(path, config, format_type))
        return self._persist_rows(ingestion_file, rows, format_type)

    def _parse_rows(self, path: Path, config: BankConfig, format_type: str) -> list[ParsedRow]:
        if format_type == "CSV":
            if config.csv is None:
                raise ValueError(f"{config.bank_code}: no CSV configuration present")
            with open_stream(path, config.csv) as stream:
                return list(CSVParser().parse(stream, config))

        if format_type == "MT940":
            if config.mt940 is None:
                raise ValueError(f"{config.bank_code}: no MT940 configuration present")
            with path.open("r", encoding="utf-8") as stream:
                return list(MT940Parser().parse(stream, config))

        if format_type == "CAMT053":
            with path.open("rb") as binary_stream:
                return list(CAMT053Parser().parse(binary_stream, config))

        # Unreachable given the check in ingest_file, but kept explicit
        # rather than relying on that earlier check alone — a future
        # refactor that calls _parse_rows directly should not silently
        # fall through with no rows.
        raise UnsupportedFormatError(f"no parser dispatch for {format_type!r}")

    def _persist_rows(
        self, ingestion_file: IngestionFile, rows: list[ParsedRow], format_type: str
    ) -> IngestionResult:
        ingested_on = datetime.now(UTC).date()
        error_count = 0
        parsed_count = 0

        for row in rows:
            parse_status = "PARSED" if row.is_valid else "QUARANTINED"
            if row.is_valid:
                parsed_count += 1
            else:
                error_count += 1

            self._repository.add_raw_transaction(
                ingestion_file_id=ingestion_file.id,
                ingested_on=ingested_on,
                bank_code=ingestion_file.bank_code,
                format_type=format_type,
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
