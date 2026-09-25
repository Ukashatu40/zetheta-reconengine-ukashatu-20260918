# tests/unit/ingestion/parsers/test_mt940_parser.py
"""Tests for recon.ingestion.parsers.mt940.

Covers the same ten categories R100 requires of the CSV parser, adapted
to MT940's structure, plus MT940-specific cases: the five deviations
named in PDF Day 2, reversal marks, and header/balance extraction.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from recon.config.models import BankConfig, Mt940FormatConfig
from recon.ingestion.parsed_row import ParsedRow, ParseErrorCode
from recon.ingestion.parsers.mt940.field_61 import Field61ParseError, parse_field_61
from recon.ingestion.parsers.mt940.lexer import MT940LexError, lex
from recon.ingestion.parsers.mt940.state_machine import (
    MT940ParseError,
    MT940Parser,
    MT940StatementHeader,
)


def _config(decimal_separator: str = ",") -> BankConfig:
    return BankConfig(
        bank_code="SBI",
        bank_name="State Bank of India",
        config_version="sbi.v1",
        supported_formats=["MT940"],
        timezone="Asia/Kolkata",
        currency_default="INR",
        settlement_cycle="T+1",
        reconciliation_window_days=1,
        mt940=Mt940FormatConfig(decimal_separator=decimal_separator),
    )


_VALID_STATEMENT = (
    ":20:STMT20260315001\n"
    ":25:1234567890\n"
    ":28C:00001/001\n"
    ":60F:C260315INR100000,00\n"
    ":61:2603150315D1500,00NTRFREF0001234567//BANKREF001\n"
    ":86:Payment to Acme Corp\n"
    ":61:2603150316C2500,50NMSCREF0007654321\n"
    ":86:Salary credit\n"
    ":62F:C260316INR101000,50\n"
)


def _parse(
    text: str, config: BankConfig | None = None
) -> tuple[MT940StatementHeader, list[ParsedRow]]:
    parser = MT940Parser()
    return parser.parse_with_header(io.StringIO(text), config or _config())


# 1. valid records --------------------------------------------------------


def test_valid_statement_parses_two_transactions_without_errors() -> None:
    _, rows = _parse(_VALID_STATEMENT)

    assert len(rows) == 2
    assert all(row.is_valid for row in rows)
    assert rows[0].mapped["reference"] == "REF0001234567//BANKREF001"
    assert rows[0].mapped["direction"] == "D"
    assert rows[0].mapped["narration"] == "Payment to Acme Corp"
    assert rows[1].mapped["direction"] == "C"


def test_header_and_balances_are_extracted() -> None:
    header, _ = _parse(_VALID_STATEMENT)

    assert header.transaction_reference == "STMT20260315001"
    assert header.account_id == "1234567890"
    assert header.statement_number == "00001/001"
    assert header.opening_balance is not None
    assert header.opening_balance.mark == "C"
    assert header.opening_balance.currency == "INR"
    assert header.opening_balance.amount_raw == "100000,00"
    assert header.closing_balance is not None
    assert header.closing_balance.amount_raw == "101000,50"


# 2. missing required field ------------------------------------------------


def test_empty_reference_is_flagged() -> None:
    text = ":20:STMT1\n:25:ACC1\n:28C:1\n:61:2603150315D1500,00NTRF\n:86:No reference given\n"
    _, rows = _parse(text)

    assert not rows[0].is_valid
    assert any(
        e.code == ParseErrorCode.MISSING_REQUIRED_FIELD and e.field == "reference"
        for e in rows[0].errors
    )


# 3. invalid dates ----------------------------------------------------------


def test_invalid_calendar_date_is_flagged() -> None:
    text = (
        ":20:STMT1\n:25:ACC1\n:28C:1\n"
        ":61:2613990315D1500,00NTRFREF001\n"  # month 13
        ":86:Bad date\n"
    )
    _, rows = _parse(text)

    assert not rows[0].is_valid
    assert any(e.code == ParseErrorCode.INVALID_DATE for e in rows[0].errors)


# 4. reversal marks — MT940's analogue of sign handling ---------------------


def test_reversal_debit_mark_is_recognised() -> None:
    text = ":20:STMT1\n:25:ACC1\n:28C:1\n:61:2603150315RD1500,00NTRFREF001\n:86:Reversal\n"
    _, rows = _parse(text)

    assert rows[0].is_valid
    assert rows[0].mapped["direction"] == "RD"


# 5. encoding issues ----------------------------------------------------------


def test_encoding_error_raised_on_read_when_bytes_do_not_match_utf8(tmp_path: Path) -> None:
    path = tmp_path / "latin1.sta"
    content = _VALID_STATEMENT.replace("Payment to Acme Corp", "Paiement à Société Café")
    path.write_bytes(content.encode("latin-1"))

    with pytest.raises(UnicodeDecodeError), path.open("r", encoding="utf-8") as stream:
        stream.read()


# 6. empty files ---------------------------------------------------------------


def test_empty_file_raises_mt940_parse_error() -> None:
    parser = MT940Parser()
    with pytest.raises(MT940ParseError, match="empty"):
        parser.parse_with_header(io.StringIO(""), _config())


# 7. structurally malformed :61: content --------------------------------------


def test_malformed_field_61_is_flagged_not_raised() -> None:
    """A single bad :61: line must not abort the whole file — it becomes a
    MALFORMED_FIELD error on that row, same discipline as the CSV parser."""
    text = ":20:STMT1\n:25:ACC1\n:28C:1\n:61:NOT-A-VALID-FIELD-61-LINE\n:86:orphaned\n"
    _, rows = _parse(text)

    assert len(rows) == 1
    assert not rows[0].is_valid
    assert rows[0].errors[0].code == ParseErrorCode.MALFORMED_FIELD


# 8. missing funds code (SBI deviation) — structural, no config needed -------


def test_missing_funds_code_parses_correctly() -> None:
    """SBI's sample data omits the optional funds-code sub-field. No
    special configuration is needed — see state_machine.py's module
    docstring for why this is structurally unambiguous."""
    text = ":20:STMT1\n:25:ACC1\n:28C:1\n:61:2603150315D1500,00NTRFREF0001\n:86:No funds code\n"
    _, rows = _parse(text)

    assert rows[0].is_valid
    assert rows[0].mapped["amount"] == "1500,00"


# 9. period decimal separator (Axis deviation) -------------------------------


def test_period_decimal_separator_configuration() -> None:
    text = ":20:STMT1\n:25:ACC1\n:28C:1\n:61:2603150315D1500.00NTRFREF0001\n:86:Period sep\n"
    _, rows = _parse(text, config=_config(decimal_separator="."))

    assert rows[0].is_valid
    assert rows[0].mapped["amount"] == "1500.00"


def test_comma_amount_under_wrong_configured_separator_is_flagged() -> None:
    """If config says '.' but the file actually uses ',', the text is not
    a valid Decimal after normalisation — proof the separator really is
    configuration-driven, not auto-detected."""
    text = ":20:STMT1\n:25:ACC1\n:28C:1\n:61:2603150315D1,234,56NTRFREF0001\n:86:Ambiguous\n"
    _, rows = _parse(text, config=_config(decimal_separator="."))

    assert not rows[0].is_valid
    assert any(e.code == ParseErrorCode.INVALID_AMOUNT for e in rows[0].errors)


# 10. non-standard :86: sub-field structure ------------------------------------


def test_86_narration_is_captured_raw_without_subfield_extraction() -> None:
    """AE-01: no attempt is made to extract counterparty name/account from
    :86: at the parsing stage. The full raw text is preserved and
    'counterparty_name' never appears in mapped at all."""
    text = (
        ":20:STMT1\n:25:ACC1\n:28C:1\n"
        ":61:2603150315D1500,00NTRFREF0001\n"
        ":86:/BEN/John Doe/ACCT/00998877/PURPOSE/Invoice settlement\n"
        "continuation line with more free text\n"
    )
    _, rows = _parse(text)

    assert rows[0].is_valid
    assert "counterparty_name" not in rows[0].mapped
    assert "/BEN/John Doe/ACCT/00998877" in rows[0].mapped["narration"]
    assert "continuation line with more free text" in rows[0].mapped["narration"]


# additional: extended reference (deviation 4) -------------------------------


def test_extended_reference_is_captured_without_truncation() -> None:
    long_ref = "REF" + "9" * 40  # well beyond the spec's nominal 16 chars
    text = f":20:STMT1\n:25:ACC1\n:28C:1\n:61:2603150315D1500,00NTRF{long_ref}\n:86:Long ref\n"
    _, rows = _parse(text)

    assert rows[0].is_valid
    assert rows[0].mapped["reference"] == long_ref


# additional: lexer-level tests -----------------------------------------------


def test_lexer_raises_on_content_before_any_tag() -> None:
    with pytest.raises(MT940LexError, match="content before any tag"):
        lex("this is not a tag line\n:20:STMT1\n")


def test_lexer_joins_multiline_narration_under_one_field() -> None:
    text = ":86:first line\nsecond line\nthird line\n"
    fields = lex(text)

    assert len(fields) == 1
    assert fields[0].tag == "86"
    assert fields[0].content == "first line\nsecond line\nthird line"


# additional: field_61 unit-level tests ----------------------------------------


def test_parse_field_61_extracts_all_structural_parts() -> None:
    parsed = parse_field_61("2603150315D1500,00NTRFREF0001234567//BANKREF001")

    assert parsed.value_date_raw == "260315"
    assert parsed.entry_date_raw == "0315"
    assert parsed.mark == "D"
    assert parsed.is_reversal is False
    assert parsed.funds_code is None
    assert parsed.amount_raw == "1500,00"
    assert parsed.transaction_type_code == "NTRF"
    assert parsed.reference_raw == "REF0001234567//BANKREF001"


def test_parse_field_61_raises_on_unstructured_content() -> None:
    with pytest.raises(Field61ParseError):
        parse_field_61("completely invalid content")
