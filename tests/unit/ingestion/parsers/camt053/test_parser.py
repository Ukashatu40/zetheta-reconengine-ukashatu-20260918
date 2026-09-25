# tests/unit/ingestion/parsers/camt053/test_parser.py
"""Tests for recon.ingestion.parsers.camt053.parser.CAMT053Parser."""

from __future__ import annotations

import io
import re

import pytest

from recon.config.models import BankConfig
from recon.ingestion.parsed_row import ParsedRow, ParseErrorCode
from recon.ingestion.parsers.camt053.parser import (
    CAMT053ParseError,
    CAMT053Parser,
    CAMT053StatementHeader,
)


def _config() -> BankConfig:
    return BankConfig(
        bank_code="KOTAK",
        bank_name="Kotak Mahindra Bank",
        config_version="kotak.v1",
        supported_formats=["CAMT053"],
        timezone="Asia/Kolkata",
        currency_default="INR",
        settlement_cycle="T+1",
        reconciliation_window_days=1,
    )


_NS = 'xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"'

_VALID_STATEMENT = f"""<?xml version="1.0" encoding="UTF-8"?>
<BkToCstmrStmt {_NS}>
  <GrpHdr>
    <MsgId>MSG20260315001</MsgId>
  </GrpHdr>
  <Stmt>
    <Id>STMT001</Id>
    <Acct>
      <Id><IBAN>IN00KOTAK1234567890</IBAN></Id>
    </Acct>
    <Bal>
      <Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp>
      <CdtDbtInd>CRDT</CdtDbtInd>
      <Amt Ccy="INR">100000.00</Amt>
      <Dt><Dt>2026-03-15</Dt></Dt>
    </Bal>
    <Bal>
      <Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp>
      <CdtDbtInd>CRDT</CdtDbtInd>
      <Amt Ccy="INR">101500.00</Amt>
      <Dt><Dt>2026-03-15</Dt></Dt>
    </Bal>
    <Ntry>
      <Amt Ccy="INR">1500.00</Amt>
      <CdtDbtInd>CRDT</CdtDbtInd>
      <BookgDt><Dt>2026-03-15</Dt></BookgDt>
      <ValDt><Dt>2026-03-16</Dt></ValDt>
      <NtryDtls>
        <TxDtls>
          <Refs><EndToEndId>E2E-REF-0001</EndToEndId></Refs>
          <RltdPties><Cdtr><Nm>Acme Corp</Nm></Cdtr></RltdPties>
          <AddtlTxInf>Payment received</AddtlTxInf>
        </TxDtls>
      </NtryDtls>
    </Ntry>
  </Stmt>
</BkToCstmrStmt>
"""


def _parse(xml_text: str) -> tuple[CAMT053StatementHeader, list[ParsedRow]]:
    parser = CAMT053Parser()
    return parser.parse_with_header(io.BytesIO(xml_text.encode()), _config())


# 1. valid records ----------------------------------------------------------


def test_valid_statement_parses_one_transaction_without_errors() -> None:
    _header, rows = _parse(_VALID_STATEMENT)

    assert len(rows) == 1
    assert rows[0].is_valid
    assert rows[0].mapped["reference"] == "E2E-REF-0001"
    assert rows[0].mapped["counterparty_name"] == "Acme Corp"
    assert rows[0].mapped["amount"] == "1500.00"
    assert rows[0].mapped["direction"] == "CRDT"


def test_header_and_balances_are_extracted() -> None:
    header, _ = _parse(_VALID_STATEMENT)

    assert header.message_id == "MSG20260315001"
    assert header.statement_id == "STMT001"
    assert header.account_id == "IN00KOTAK1234567890"
    assert len(header.balances) == 2
    assert header.balances[0].type_code == "OPBD"
    assert header.balances[1].type_code == "CLBD"
    assert header.balances[1].amount_raw == "101500.00"


# AE-02: this is the load-bearing assertion for the whole increment -------


def test_settlement_date_is_per_entry_val_dt_not_statement_balance_date() -> None:
    """AE-02: settlement_date must come from Ntry/ValDt/Dt (2026-03-16 in
    the fixture), which is deliberately DIFFERENT from both the entry's
    own BookgDt (2026-03-15) and the statement-level CLBD balance date
    (also 2026-03-15) — proving settlement_date is not accidentally
    sourced from either of those instead."""
    _, rows = _parse(_VALID_STATEMENT)

    assert rows[0].mapped["txn_date"] == "2026-03-15"  # BookgDt
    assert rows[0].mapped["settlement_date"] == "2026-03-16"  # ValDt — distinct


# 2. missing required field --------------------------------------------------


def test_missing_end_to_end_id_is_flagged() -> None:
    xml = _VALID_STATEMENT.replace(
        "<Refs><EndToEndId>E2E-REF-0001</EndToEndId></Refs>", "<Refs></Refs>"
    )
    _, rows = _parse(xml)

    assert not rows[0].is_valid
    assert any(
        e.code == ParseErrorCode.MISSING_REQUIRED_FIELD and e.field == "reference"
        for e in rows[0].errors
    )


# 3. invalid / missing dates -------------------------------------------------


def test_missing_bookg_dt_is_flagged() -> None:
    xml = _VALID_STATEMENT.replace("<BookgDt><Dt>2026-03-15</Dt></BookgDt>", "")
    _, rows = _parse(xml)

    assert not rows[0].is_valid
    assert any(e.field == "txn_date" for e in rows[0].errors)


# 4. malformed direction (CAMT's analogue of "negative amount") -------------


def test_invalid_credit_debit_indicator_is_flagged() -> None:
    """Targets the Ntry's CdtDbtInd specifically, not either Bal block's —
    matched via the whitespace that separates it from the following
    BookgDt element, since that pairing only occurs once in the fixture."""
    xml = re.sub(
        r"<CdtDbtInd>CRDT</CdtDbtInd>(\s*<BookgDt>)",
        r"<CdtDbtInd>WEIRD</CdtDbtInd>\1",
        _VALID_STATEMENT,
        count=1,
    )
    _, rows = _parse(xml)

    assert not rows[0].is_valid
    assert any(
        e.code == ParseErrorCode.MALFORMED_FIELD and e.field == "direction" for e in rows[0].errors
    )


# 5. encoding — CAMT declares its own encoding in the XML prolog ------------


def test_declared_encoding_is_honoured() -> None:
    xml = _VALID_STATEMENT.replace("Acme Corp", "Café Société")
    _, rows = _parse(xml)

    assert rows[0].mapped["counterparty_name"] == "Café Société"


# 6. empty / malformed files -------------------------------------------------


def test_empty_stream_raises_camt053_parse_error() -> None:
    parser = CAMT053Parser()
    with pytest.raises(CAMT053ParseError, match="malformed XML"):
        parser.parse_with_header(io.BytesIO(b""), _config())


def test_truncated_xml_raises_camt053_parse_error() -> None:
    truncated = _VALID_STATEMENT[: len(_VALID_STATEMENT) // 2]
    parser = CAMT053Parser()
    with pytest.raises(CAMT053ParseError, match="malformed XML"):
        parser.parse_with_header(io.BytesIO(truncated.encode()), _config())


# 7. wrong root element -------------------------------------------------------


def test_wrong_root_element_raises_camt053_parse_error() -> None:
    wrong_root = f'<?xml version="1.0"?><NotAStatement {_NS}></NotAStatement>'
    parser = CAMT053Parser()
    with pytest.raises(CAMT053ParseError, match="does not look like"):
        parser.parse_with_header(io.BytesIO(wrong_root.encode()), _config())


# 8. deeply nested NtryDtls/TxDtls (R28) --------------------------------------


def test_multiple_txdtls_under_one_ntry_uses_first_populated_values() -> None:
    xml = _VALID_STATEMENT.replace(
        "</NtryDtls>",
        (
            "<TxDtls>"
            "<Refs><EndToEndId>E2E-REF-SECOND</EndToEndId></Refs>"
            "<AddtlTxInf>Second leg</AddtlTxInf>"
            "</TxDtls>"
            "</NtryDtls>"
        ),
    )
    _, rows = _parse(xml)

    assert rows[0].is_valid
    assert rows[0].mapped["reference"] == "E2E-REF-0001"  # first TxDtls wins
    assert "Second leg" in rows[0].mapped["narration"]  # but narration concatenates both


def test_ntry_with_no_ntrydtls_at_all_is_still_parseable() -> None:
    xml = re.sub(
        r"<NtryDtls>.*?</NtryDtls>",
        "<AddtlNtryInf>Entry-level note only, no TxDtls</AddtlNtryInf>",
        _VALID_STATEMENT,
        count=1,
        flags=re.DOTALL,
    )
    _, rows = _parse(xml)

    assert not rows[0].is_valid  # no EndToEndId anywhere -> missing reference
    assert "counterparty_name" not in rows[0].mapped
    assert rows[0].mapped["narration"] == "Entry-level note only, no TxDtls"


# 9. multiple Ntry elements in one statement ---------------------------------


def test_multiple_entries_produce_multiple_rows_with_correct_line_numbers() -> None:
    second_entry = _VALID_STATEMENT.split("<Ntry>")[1].split("</Ntry>", maxsplit=1)[0]
    xml = _VALID_STATEMENT.replace(
        "</Ntry>\n  </Stmt>",
        f"""</Ntry>\n  <Ntry>{
            second_entry.replace('E2E-REF-0001', 'E2E-REF-0002')
            }</Ntry>\n  </Stmt>""",
    )
    _, rows = _parse(xml)

    assert len(rows) == 2
    assert rows[0].line_no == 1
    assert rows[1].line_no == 2
    assert rows[1].mapped["reference"] == "E2E-REF-0002"


# 10. missing currency / amount ------------------------------------------------


def test_missing_currency_attribute_is_flagged() -> None:
    xml = _VALID_STATEMENT.replace('<Amt Ccy="INR">1500.00</Amt>', "<Amt>1500.00</Amt>")
    _, rows = _parse(xml)

    assert not rows[0].is_valid
    assert any(e.field == "currency" for e in rows[0].errors)


def test_missing_amount_element_is_flagged() -> None:
    xml = _VALID_STATEMENT.replace('<Amt Ccy="INR">1500.00</Amt>', "", 1)
    # NOTE: the first two <Amt Ccy="INR">...</Amt> occurrences belong to the
    # Bal elements; this replace(..., 1) with the Ntry's Amt appearing after
    # both Bal blocks in fixture order actually hits the OPBD balance first.
    # Use a more targeted replacement instead:
    xml = _VALID_STATEMENT.replace(
        '<Amt Ccy="INR">1500.00</Amt>\n      <CdtDbtInd>CRDT</CdtDbtInd>\n      <BookgDt>',
        "<CdtDbtInd>CRDT</CdtDbtInd>\n      <BookgDt>",
    )
    _, rows = _parse(xml)

    assert not rows[0].is_valid
    assert any(e.field == "amount" for e in rows[0].errors)
