# src/recon/ingestion/normalisation_service.py
"""NormalisationService: reads PARSED RawTransaction rows for a given
ingestion file, reconstructs each as a ParsedRow, runs it through
NormalisationPipeline, and persists the result as a NormalisedTransaction.

The reconstruction step (JSONB raw_payload -> ParsedRow) is the seam
described in this increment's module-level design note: IngestionService
(WP2) wrote raw_payload as {"raw_fields": {...}, "mapped": {...}}, and
this is the one place that shape is read back. A key rename on either
side would silently break this without a test that exercises the actual
round trip — see tests/integration/test_normalisation_service.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from recon.config.models import BankConfig
from recon.domain.enums import SourceFormat, TransactionSource
from recon.ingestion.parsed_row import ParsedRow
from recon.normalisation.pipeline import NormalisationError, NormalisationPipeline
from recon.persistence.models import RawTransaction
from recon.persistence.repositories.normalisation import NormalisationRepository


@dataclass(frozen=True, slots=True)
class NormalisationResult:
    normalised_count: int
    failed_count: int
    failures: list[
        str
    ]  # human-readable detail per failure, for visibility until WP4's exception engine exists


class NormalisationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = NormalisationRepository(session)
        self._pipeline = NormalisationPipeline()

    def normalise_ingestion_file(
        self,
        ingestion_file_id: str,
        config: BankConfig,
        format_type: SourceFormat,
        source: TransactionSource,
    ) -> NormalisationResult:

        raw_rows = self._repository.find_parsed_raw_transactions(uuid.UUID(ingestion_file_id))

        normalised_count = 0
        failures: list[str] = []

        for raw_row in raw_rows:
            parsed_row = self._reconstruct_parsed_row(raw_row)
            try:
                canonical = self._pipeline.normalise(
                    parsed_row, config, format_type, source, ingestion_file_id
                )
            except NormalisationError as exc:
                failures.append(str(exc))
                continue

            self._repository.save(canonical, raw_row.id)
            normalised_count += 1

        return NormalisationResult(
            normalised_count=normalised_count,
            failed_count=len(failures),
            failures=failures,
        )

    @staticmethod
    def _reconstruct_parsed_row(raw: RawTransaction) -> ParsedRow:
        """Reverses IngestionService._row_to_payload's shape. raw.raw_payload
        is a JSONB column, so SQLAlchemy hands it back as a plain dict —
        no additional decoding needed, but the two expected keys are
        accessed explicitly (not with .get(..., {})) so a shape mismatch
        raises a clear KeyError immediately rather than silently producing
        an empty ParsedRow that then fails normalisation confusingly."""
        raw_fields = raw.raw_payload["raw_fields"]
        mapped = raw.raw_payload["mapped"]
        return ParsedRow(
            line_no=raw.source_line_no or 0,
            raw_fields=raw_fields,
            mapped=mapped,
            errors=[],  # only PARSED rows reach here (see repository), so no errors by construction
        )
