# tests/integration/test_ingestion_service.py
"""Integration tests for IngestionService: the full path from a CSV file
on disk to persisted ingestion_files + raw_transactions rows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from recon.config.loader import load_bank_config
from recon.ingestion.service import IngestionService, UnsupportedFormatError
from recon.persistence.models import IngestionFile, RawTransaction
from recon.persistence.repositories.ingestion import DuplicateFileError

_HDFC_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "banks" / "hdfc.v1.yaml"
_SBI_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "banks" / "sbi.v1.yaml"
_KOTAK_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "banks" / "kotak.v1.yaml"


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
    result = service.ingest_file(csv_path, config, "CSV")
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
    result = service.ingest_file(csv_path, config, "CSV")
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
    first_result = service.ingest_file(first_path, config, "CSV")
    db_session.flush()

    with pytest.raises(DuplicateFileError):
        service.ingest_file(second_path, config, "CSV")

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
    result = service.ingest_file(csv_path, config, "CSV")
    db_session.flush()

    assert result.status == "FAILED"
    assert result.parsed_count == 0
    assert result.error_count == 1


def test_ingest_valid_mt940_file_creates_file_and_transaction_rows(
    db_session: Session, tmp_path: Path
) -> None:
    config = load_bank_config(_SBI_CONFIG_PATH)
    mt940_path = tmp_path / "sbi_statement.sta"
    mt940_path.write_text(
        ":20:STMT20260315001\n"
        ":25:1234567890\n"
        ":28C:00001/001\n"
        ":60F:C260315INR100000,00\n"
        ":61:2603150315D1500,00NTRFREF0001234567\n"
        ":86:Payment to Acme Corp\n"
        ":62F:C260316INR98500,00\n",
        encoding="utf-8",
    )

    service = IngestionService(db_session, ingested_by="test-suite")
    result = service.ingest_file(mt940_path, config, "MT940")
    db_session.flush()

    assert result.status == "PARSED"
    assert result.record_count == 1
    assert result.error_count == 0

    file_row = db_session.get(IngestionFile, result.ingestion_file_id)
    assert file_row is not None
    assert file_row.format_type == "MT940"

    txn_rows = (
        db_session.query(RawTransaction)
        .filter(RawTransaction.ingestion_file_id == file_row.id)
        .all()
    )
    assert len(txn_rows) == 1
    assert txn_rows[0].raw_payload["mapped"]["reference"] == "REF0001234567"


def test_ingest_valid_camt053_file_creates_file_and_transaction_rows(
    db_session: Session, tmp_path: Path
) -> None:
    config = load_bank_config(_KOTAK_CONFIG_PATH)
    camt_path = tmp_path / "kotak_statement.xml"
    camt_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<BkToCstmrStmt xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">\n'
        "  <GrpHdr><MsgId>MSG20260315001</MsgId></GrpHdr>\n"
        "  <Stmt>\n"
        "    <Id>STMT001</Id>\n"
        "    <Ntry>\n"
        '      <Amt Ccy="INR">1500.00</Amt>\n'
        "      <CdtDbtInd>CRDT</CdtDbtInd>\n"
        "      <BookgDt><Dt>2026-03-15</Dt></BookgDt>\n"
        "      <ValDt><Dt>2026-03-16</Dt></ValDt>\n"
        "      <NtryDtls>\n"
        "        <TxDtls>\n"
        "          <Refs><EndToEndId>E2E-REF-0001</EndToEndId></Refs>\n"
        "          <RltdPties><Cdtr><Nm>Acme Corp</Nm></Cdtr></RltdPties>\n"
        "        </TxDtls>\n"
        "      </NtryDtls>\n"
        "    </Ntry>\n"
        "  </Stmt>\n"
        "</BkToCstmrStmt>\n",
        encoding="utf-8",
    )

    service = IngestionService(db_session, ingested_by="test-suite")
    result = service.ingest_file(camt_path, config, "CAMT053")
    db_session.flush()

    assert result.status == "PARSED"
    assert result.record_count == 1

    file_row = db_session.get(IngestionFile, result.ingestion_file_id)
    assert file_row is not None
    assert file_row.format_type == "CAMT053"

    txn_rows = (
        db_session.query(RawTransaction)
        .filter(RawTransaction.ingestion_file_id == file_row.id)
        .all()
    )
    assert len(txn_rows) == 1
    assert txn_rows[0].raw_payload["mapped"]["reference"] == "E2E-REF-0001"
    assert txn_rows[0].raw_payload["mapped"]["settlement_date"] == "2026-03-16"


def test_ingest_file_rejects_format_not_in_bank_config(db_session: Session, tmp_path: Path) -> None:
    """AXIS's config supports CSV and MT940, not CAMT053 — asking to
    ingest a file as CAMT053 against that config must be rejected before
    any hashing or persistence happens."""
    config = load_bank_config(
        Path(__file__).resolve().parents[2] / "config" / "banks" / "axis.v1.yaml"
    )
    fake_camt_path = tmp_path / "not_really_camt.xml"
    fake_camt_path.write_text("<root/>", encoding="utf-8")

    service = IngestionService(db_session, ingested_by="test-suite")
    with pytest.raises(UnsupportedFormatError, match="does not list"):
        service.ingest_file(fake_camt_path, config, "CAMT053")


def test_ingest_file_rejects_entirely_unknown_format(db_session: Session, tmp_path: Path) -> None:
    config = load_bank_config(_HDFC_CONFIG_PATH)
    path = tmp_path / "whatever.dat"
    path.write_text("irrelevant", encoding="utf-8")

    service = IngestionService(db_session, ingested_by="test-suite")
    with pytest.raises(UnsupportedFormatError, match="not supported"):
        service.ingest_file(path, config, "FTP_LEGACY_FORMAT")
