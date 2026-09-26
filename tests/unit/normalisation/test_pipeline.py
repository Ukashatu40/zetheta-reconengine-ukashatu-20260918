# tests/unit/normalisation/test_pipeline.py
"""Tests for recon.normalisation.pipeline.NormalisationPipeline.

Two kinds of coverage: hand-built ParsedRow objects for isolated edge
cases (invalid row rejection, currency fallback, the local_date fix
specifically), and round-trip tests that run a real format parser
(CSV/MT940/CAMT053) against a real bank config to produce the ParsedRow
the pipeline actually receives in production — proving the two layers
compose correctly, not just that each behaves correctly in isolation.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pytest

from recon.config.loader import load_bank_config
from recon.config.models import BankConfig
from recon.domain.enums import Direction, SourceFormat, TransactionSource
from recon.ingestion.parsed_row import ParsedRow, ParseError, ParseErrorCode
from recon.ingestion.parsers.camt053.parser import CAMT053Parser
from recon.ingestion.parsers.csv_parser import CSVParser
from recon.ingestion.parsers.mt940.state_machine import MT940Parser
from recon.normalisation.pipeline import NormalisationError, NormalisationPipeline

_BANKS_DIR = Path(__file__).resolve().parents[3] / "config" / "banks"


def _hdfc_config() -> BankConfig:
    return load_bank_config(_BANKS_DIR / "hdfc.v1.yaml")


def _sbi_config() -> BankConfig:
    return load_bank_config(_BANKS_DIR / "sbi.v1.yaml")


def _kotak_config() -> BankConfig:
    return load_bank_config(_BANKS_DIR / "kotak.v1.yaml")


# --- hand-built ParsedRow edge cases -----------------------------------


def test_invalid_row_raises_normalisation_error() -> None:
    row = ParsedRow(
        line_no=2,
        raw_fields={},
        mapped={},
        errors=[
            ParseError(
                code=ParseErrorCode.MISSING_REQUIRED_FIELD, field="reference", detail="empty"
            )
        ],
    )
    pipeline = NormalisationPipeline()

    with pytest.raises(NormalisationError, match="existing parse errors"):
        pipeline.normalise(
            row, _hdfc_config(), SourceFormat.CSV, TransactionSource.EXTERNAL, "file-1"
        )


def test_unrecognised_direction_raises_normalisation_error() -> None:
    row = ParsedRow(
        line_no=2,
        raw_fields={},
        mapped={
            "reference": "REF001",
            "txn_date": "15-03-2026",
            "amount": "100.00",
            "direction": "SIDEWAYS",
        },
        errors=[],
    )
    pipeline = NormalisationPipeline()

    with pytest.raises(NormalisationError, match="not a recognised direction"):
        pipeline.normalise(
            row, _hdfc_config(), SourceFormat.CSV, TransactionSource.EXTERNAL, "file-1"
        )


def test_currency_falls_back_to_bank_default_when_absent_from_row() -> None:
    row = ParsedRow(
        line_no=2,
        raw_fields={},
        mapped={
            "reference": "REF001",
            "txn_date": "15-03-2026",
            "amount": "100.00",
            "direction": "CR",
        },
        errors=[],
    )
    pipeline = NormalisationPipeline()

    txn = pipeline.normalise(
        row, _hdfc_config(), SourceFormat.CSV, TransactionSource.EXTERNAL, "file-1"
    )

    assert txn.currency == "INR"  # HDFC's configured currency_default


def test_txn_date_uses_local_calendar_date_not_utc_shifted_date() -> None:
    """The exact defect fixed in this increment: for HDFC (Asia/Kolkata,
    UTC+5:30), a date-only value must produce a canonical txn_date
    matching the statement's own date — NOT one day earlier, which a
    naive UTC-instant-based date would silently produce."""
    row = ParsedRow(
        line_no=2,
        raw_fields={},
        mapped={
            "reference": "REF001",
            "txn_date": "15-03-2026",
            "amount": "100.00",
            "direction": "CR",
        },
        errors=[],
    )
    pipeline = NormalisationPipeline()

    txn = pipeline.normalise(
        row, _hdfc_config(), SourceFormat.CSV, TransactionSource.EXTERNAL, "file-1"
    )

    assert txn.txn_date == date(2026, 3, 15)
    assert txn.txn_timestamp_utc.date() == date(2026, 3, 14)  # shifted UTC instant, preserved


# --- round-trip: real parser output through the pipeline ----------------


def test_csv_round_trip_through_pipeline() -> None:
    config = _hdfc_config()
    csv_text = (
        "Transaction Reference,Value Date,Amount,Dr/Cr,Counterparty Name,Narration\n"
        "REF001,15-03-2026,1500.00,CR,Acme Corp,Payment received\n"
    )
    row = next(CSVParser().parse(io.StringIO(csv_text), config))
    assert row.is_valid

    pipeline = NormalisationPipeline()
    txn = pipeline.normalise(row, config, SourceFormat.CSV, TransactionSource.EXTERNAL, "file-1")

    assert txn.txn_date == date(2026, 3, 15)
    assert txn.direction == Direction.CR
    assert txn.is_reversal is False
    assert txn.amount_minor == 150_000
    assert txn.currency == "INR"
    assert txn.normalised_reference == "REF001"
    assert txn.counterparty_name_normalised == "ACME CORPORATION"


def test_mt940_round_trip_through_pipeline_including_reversal() -> None:
    config = _sbi_config()
    mt940_text = (
        ":20:STMT1\n:25:ACC1\n:28C:1\n"
        ":61:2603150315RD1500,00NTRFREF0001234567\n"
        ":86:Reversal of prior debit\n"
    )
    row = next(MT940Parser().parse(io.StringIO(mt940_text), config))
    assert row.is_valid

    pipeline = NormalisationPipeline()
    txn = pipeline.normalise(row, config, SourceFormat.MT940, TransactionSource.EXTERNAL, "file-1")

    assert txn.direction == Direction.DR
    assert txn.is_reversal is True
    assert txn.currency == "INR"  # falls back to SBI's bank config default
    assert txn.counterparty_name is None  # AE-01: MT940 never produces this field


def test_camt053_round_trip_preserves_settlement_date_distinct_from_txn_date() -> None:
    """AE-02, carried all the way through to the canonical model: the
    pipeline must not collapse txn_date and settlement_date into one
    value even though both pass through the same date-parsing machinery."""
    config = _kotak_config()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<BkToCstmrStmt xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">\n'
        "  <GrpHdr><MsgId>MSG1</MsgId></GrpHdr>\n"
        "  <Stmt><Id>STMT1</Id>\n"
        "    <Ntry>\n"
        '      <Amt Ccy="INR">1500.00</Amt>\n'
        "      <CdtDbtInd>CRDT</CdtDbtInd>\n"
        "      <BookgDt><Dt>2026-03-15</Dt></BookgDt>\n"
        "      <ValDt><Dt>2026-03-16</Dt></ValDt>\n"
        "      <NtryDtls><TxDtls>\n"
        "        <Refs><EndToEndId>E2E-0001</EndToEndId></Refs>\n"
        "        <RltdPties><Cdtr><Nm>Acme Corp</Nm></Cdtr></RltdPties>\n"
        "      </TxDtls></NtryDtls>\n"
        "    </Ntry>\n"
        "  </Stmt>\n"
        "</BkToCstmrStmt>\n"
    )
    row = next(CAMT053Parser().parse(io.BytesIO(xml.encode()), config))
    assert row.is_valid

    pipeline = NormalisationPipeline()
    txn = pipeline.normalise(
        row, config, SourceFormat.CAMT053, TransactionSource.EXTERNAL, "file-1"
    )

    assert txn.txn_date == date(2026, 3, 15)
    assert txn.settlement_date == date(2026, 3, 16)
    assert txn.currency == "INR"
    assert txn.direction == Direction.CR
