# tests/integration/test_normalisation_service.py
"""Integration tests for NormalisationService: the full path from
IngestionService's persisted RawTransaction rows through
NormalisationPipeline to persisted NormalisedTransaction rows.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from recon.config.loader import load_bank_config
from recon.domain.enums import SourceFormat, TransactionSource
from recon.ingestion.normalisation_service import NormalisationService
from recon.ingestion.service import IngestionService
from recon.persistence.models import NormalisedTransaction
from recon.persistence.repositories.normalisation import NormalisationRepository

_HDFC_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "banks" / "hdfc.v1.yaml"
_HEADER = "Transaction Reference,Value Date,Amount,Dr/Cr,Counterparty Name,Narration"


def test_normalise_ingestion_file_persists_canonical_rows(
    db_session: Session, tmp_path: Path
) -> None:
    config = load_bank_config(_HDFC_CONFIG_PATH)
    csv_path = tmp_path / "hdfc.csv"
    csv_path.write_text(
        f"{_HEADER}\n"
        "REF001,15-03-2026,1500.00,CR,Acme Corp,Payment received\n"
        "REF002,16-03-2026,250.50,DR,Beta Ltd,Refund issued\n",
        encoding="utf-8",
    )

    ingestion = IngestionService(db_session, ingested_by="test-suite")
    ingest_result = ingestion.ingest_file(csv_path, config, "CSV")
    db_session.flush()

    normalisation = NormalisationService(db_session)
    norm_result = normalisation.normalise_ingestion_file(
        ingest_result.ingestion_file_id, config, SourceFormat.CSV, TransactionSource.EXTERNAL
    )
    db_session.flush()

    assert norm_result.normalised_count == 2
    assert norm_result.failed_count == 0

    rows = (
        db_session.query(NormalisedTransaction)
        .filter(NormalisedTransaction.ingestion_file_id == ingest_result.ingestion_file_id)
        .order_by(NormalisedTransaction.source_line_no)
        .all()
    )
    assert len(rows) == 2
    assert rows[0].normalised_reference == "REF001"
    assert rows[0].amount_minor == 150_000
    assert rows[0].direction == "CR"
    assert rows[0].counterparty_name_normalised == "ACME CORPORATION"


def test_quarantined_rows_are_excluded_from_normalisation(
    db_session: Session, tmp_path: Path
) -> None:
    """Explicit test for this increment's documented gap: a row that
    already failed structural parsing is neither normalised NOR raised as
    a normalisation failure — it's silently excluded, pending WP4's
    exception engine actually surfacing it."""
    config = load_bank_config(_HDFC_CONFIG_PATH)
    csv_path = tmp_path / "hdfc_mixed.csv"
    csv_path.write_text(
        f"{_HEADER}\n"
        "REF001,15-03-2026,1500.00,CR,Acme Corp,Payment received\n"
        ",16-03-2026,not-a-number,DR,Beta Ltd,Refund issued\n",  # will be QUARANTINED
        encoding="utf-8",
    )

    ingestion = IngestionService(db_session, ingested_by="test-suite")
    ingest_result = ingestion.ingest_file(csv_path, config, "CSV")
    assert ingest_result.error_count == 1  # confirms one row really was quarantined
    db_session.flush()

    normalisation = NormalisationService(db_session)
    norm_result = normalisation.normalise_ingestion_file(
        ingest_result.ingestion_file_id, config, SourceFormat.CSV, TransactionSource.EXTERNAL
    )
    db_session.flush()

    assert norm_result.normalised_count == 1  # only the valid row
    assert norm_result.failed_count == 0  # the bad row never reached the pipeline at all

    rows = (
        db_session.query(NormalisedTransaction)
        .filter(NormalisedTransaction.ingestion_file_id == ingest_result.ingestion_file_id)
        .all()
    )
    assert len(rows) == 1


def test_reconstructed_parsed_row_matches_original_mapped_fields(
    db_session: Session, tmp_path: Path
) -> None:
    """Direct test of the JSONB round-trip seam described in this
    module's docstring: what NormalisationService reconstructs from
    raw_payload must match what IngestionService originally wrote."""
    config = load_bank_config(_HDFC_CONFIG_PATH)
    csv_path = tmp_path / "hdfc_single.csv"
    csv_path.write_text(
        f"{_HEADER}\nREF001,15-03-2026,1500.00,CR,Acme Corp,Payment received\n",
        encoding="utf-8",
    )

    ingestion = IngestionService(db_session, ingested_by="test-suite")
    ingest_result = ingestion.ingest_file(csv_path, config, "CSV")
    db_session.flush()

    repo = NormalisationRepository(db_session)
    raw_rows = repo.find_parsed_raw_transactions(uuid.UUID(ingest_result.ingestion_file_id))
    assert len(raw_rows) == 1

    service = NormalisationService(db_session)
    reconstructed = service._reconstruct_parsed_row(raw_rows[0])

    assert reconstructed.mapped["reference"] == "REF001"
    assert reconstructed.mapped["amount"] == "1500.00"
    assert reconstructed.is_valid is True
