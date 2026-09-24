# tests/integration/test_ingestion_service.py
"""Integration tests for IngestionService: the full path from a CSV file
on disk to persisted ingestion_files + raw_transactions rows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from recon.config.loader import load_bank_config
from recon.ingestion.service import IngestionService
from recon.persistence.models import IngestionFile, RawTransaction
from recon.persistence.repositories.ingestion import DuplicateFileError

_HDFC_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "banks" / "hdfc.v1.yaml"

_HEADER = "Transaction Reference,Value Date,Amount,Dr/Cr,Counterparty Name,Narration"


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(f"{_HEADER}\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_ingest_valid_file_creates_file_and_transaction_rows(
    db_session: Session, tmp_path: Path
) -> None:
    config = load_bank_config(_HDFC_CONFIG_PATH)
    csv_path = _write_csv(
        tmp_path / "hdfc_settlement.csv",
        [
            "REF001,15-03-2026,1500.00,CR,Acme Corp,Payment received",
            "REF002,16-03-2026,250.50,DR,Beta Ltd,Refund issued",
        ],
    )

    service = IngestionService(db_session, ingested_by="test-suite")
    result = service.ingest_csv_file(csv_path, config)
    db_session.flush()

    assert result.status == "PARSED"
    assert result.record_count == 2
    assert result.parsed_count == 2
    assert result.error_count == 0

    file_row = db_session.get(IngestionFile, result.ingestion_file_id)
    assert file_row is not None
    assert file_row.status == "PARSED"
    assert file_row.record_count == 2

    txn_rows = (
        db_session.query(RawTransaction)
        .filter(RawTransaction.ingestion_file_id == file_row.id)
        .order_by(RawTransaction.source_line_no)
        .all()
    )
    assert len(txn_rows) == 2
    assert txn_rows[0].parse_status == "PARSED"
    assert txn_rows[0].raw_payload["mapped"]["reference"] == "REF001"


def test_ingest_file_with_some_malformed_rows_is_partially_parsed(
    db_session: Session, tmp_path: Path
) -> None:
    config = load_bank_config(_HDFC_CONFIG_PATH)
    csv_path = _write_csv(
        tmp_path / "hdfc_partial.csv",
        [
            "REF001,15-03-2026,1500.00,CR,Acme Corp,Payment received",
            ",16-03-2026,not-a-number,DR,Beta Ltd,Refund issued",  # missing ref, bad amount
        ],
    )

    service = IngestionService(db_session, ingested_by="test-suite")
    result = service.ingest_csv_file(csv_path, config)
    db_session.flush()

    assert result.status == "PARTIALLY_PARSED"
    assert result.parsed_count == 1
    assert result.error_count == 1

    quarantined = (
        db_session.query(RawTransaction).filter(RawTransaction.parse_status == "QUARANTINED").one()
    )
    assert quarantined.parse_errors is not None
    error_codes = {e["code"] for e in quarantined.parse_errors["errors"]}
    assert "MISSING_REQUIRED_FIELD" in error_codes
    assert "INVALID_AMOUNT" in error_codes


def test_duplicate_file_content_is_rejected_before_any_transactions_are_written(
    db_session: Session, tmp_path: Path
) -> None:
    """B4.4: content-hash duplicate detection. The second ingestion must
    raise before writing anything to raw_transactions, even under a
    different filename."""
    config = load_bank_config(_HDFC_CONFIG_PATH)
    content = ["REF001,15-03-2026,1500.00,CR,Acme Corp,Payment received"]
    first_path = _write_csv(tmp_path / "hdfc_original.csv", content)
    second_path = _write_csv(tmp_path / "hdfc_resubmitted.csv", content)

    service = IngestionService(db_session, ingested_by="test-suite")
    first_result = service.ingest_csv_file(first_path, config)
    db_session.flush()

    with pytest.raises(DuplicateFileError):
        service.ingest_csv_file(second_path, config)

    txn_count = (
        db_session.query(RawTransaction)
        .filter(RawTransaction.ingestion_file_id == first_result.ingestion_file_id)
        .count()
    )
    assert txn_count == 1  # only the first file's row exists


def test_fully_malformed_file_status_is_failed(db_session: Session, tmp_path: Path) -> None:
    config = load_bank_config(_HDFC_CONFIG_PATH)
    csv_path = _write_csv(
        tmp_path / "hdfc_all_bad.csv",
        [",15-03-2026,not-a-number,CR,Acme,Note"],
    )

    service = IngestionService(db_session, ingested_by="test-suite")
    result = service.ingest_csv_file(csv_path, config)
    db_session.flush()

    assert result.status == "FAILED"
    assert result.parsed_count == 0
    assert result.error_count == 1
