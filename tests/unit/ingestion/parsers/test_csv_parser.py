# tests/unit/ingestion/parsers/test_csv_parser.py
"""Tests for recon.ingestion.parsers.csv_parser.CSVParser.

Covers the ten categories R100 (PDF Day 1) requires: valid records,
missing fields, invalid dates, negative amounts, encoding issues, empty
files, duplicate headers, extra columns, truncated rows, and Unicode
characters — plus a few additional cases (invalid amount text, line
numbering) that fall naturally out of implementing the above.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from recon.config.models import BankConfig, CsvFormatConfig
from recon.ingestion.parsed_row import ParsedRow, ParseErrorCode
from recon.ingestion.parsers.csv_parser import CSVParseError, CSVParser, open_stream

_HEADER = "Transaction Reference,Value Date,Amount,Dr/Cr,Counterparty Name,Narration"


def _hdfc_config() -> BankConfig:
    return BankConfig(
        bank_code="HDFC",
        bank_name="HDFC Bank",
        config_version="hdfc.v1",
        supported_formats=["CSV"],
        timezone="Asia/Kolkata",
        currency_default="INR",
        settlement_cycle="T+1",
        reconciliation_window_days=1,
        csv=CsvFormatConfig(
            date_format="%d-%m-%Y",
            column_mapping={
                "reference": "Transaction Reference",
                "txn_date": "Value Date",
                "amount": "Amount",
                "direction": "Dr/Cr",
                "counterparty_name": "Counterparty Name",
                "narration": "Narration",
            },
        ),
    )


def _parse(csv_text: str) -> list[ParsedRow]:
    parser = CSVParser()
    return list(parser.parse(io.StringIO(csv_text), _hdfc_config()))


# 1. valid records --------------------------------------------------------


def test_valid_records_parse_without_errors() -> None:
    csv_text = (
        f"{_HEADER}\n"
        "REF001,15-03-2026,1500.00,CR,Acme Corp,Payment received\n"
        "REF002,16-03-2026,250.50,DR,Beta Ltd,Refund issued\n"
    )
    rows = _parse(csv_text)

    assert len(rows) == 2
    assert all(row.is_valid for row in rows)
    assert rows[0].mapped["reference"] == "REF001"
    assert rows[0].mapped["amount"] == "1500.00"


# 2. missing fields ---------------------------------------------------------


def test_missing_required_field_is_flagged_when_value_is_empty() -> None:
    csv_text = f"{_HEADER}\n,15-03-2026,1500.00,CR,Acme Corp,Note\n"
    rows = _parse(csv_text)

    assert not rows[0].is_valid
    codes = {e.code for e in rows[0].errors}
    assert ParseErrorCode.MISSING_REQUIRED_FIELD in codes
    assert any(e.field == "reference" for e in rows[0].errors)


def test_missing_required_field_is_flagged_when_source_column_absent() -> None:
    """Header itself doesn't have the mapped column at all — a config/file
    mismatch, distinct from a row simply having an empty value."""
    bad_header = "Ref,Date,Amount,Direction"  # no "Dr/Cr", "Counterparty Name", "Narration"
    csv_text = f"{bad_header}\nREF001,15-03-2026,1500.00,CR\n"
    rows = _parse(csv_text)

    assert not rows[0].is_valid
    assert any(
        e.code == ParseErrorCode.MISSING_REQUIRED_FIELD and e.field == "direction"
        for e in rows[0].errors
    )


# 3. invalid dates -----------------------------------------------------------


def test_invalid_date_is_flagged() -> None:
    csv_text = f"{_HEADER}\nREF001,2026/03/15,1500.00,CR,Acme,Note\n"  # wrong format for HDFC
    rows = _parse(csv_text)

    assert not rows[0].is_valid
    assert any(e.code == ParseErrorCode.INVALID_DATE for e in rows[0].errors)


def test_invalid_date_calendar_value_is_flagged() -> None:
    csv_text = f"{_HEADER}\nREF001,31-02-2026,1500.00,CR,Acme,Note\n"  # no such date
    rows = _parse(csv_text)

    assert any(e.code == ParseErrorCode.INVALID_DATE for e in rows[0].errors)


# 4. negative amounts ---------------------------------------------------------


def test_negative_amount_parses_without_a_parse_error() -> None:
    """Sign-vs-direction consistency is a normalisation-stage rule
    (CanonicalTransaction's validator, AE-05), not a parsing-stage one.
    The raw text is captured as-is; rejecting it happens one stage later."""
    csv_text = f"{_HEADER}\nREF001,15-03-2026,-1500.00,CR,Acme,Note\n"
    rows = _parse(csv_text)

    assert rows[0].is_valid
    assert rows[0].mapped["amount"] == "-1500.00"


# 5. encoding issues -----------------------------------------------------------


def test_encoding_error_raised_on_read_when_bytes_do_not_match_configured_encoding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "latin1.csv"
    # "é" is not valid in strict ascii/utf-8-only reading when the file was
    # actually written as latin-1
    content = f"{_HEADER}\nREF001,15-03-2026,100.00,CR,Café Corp,Note\n"
    path.write_bytes(content.encode("latin-1"))

    csv_config = _hdfc_config().csv
    assert csv_config is not None

    with pytest.raises(UnicodeDecodeError), open_stream(path, csv_config) as stream:
        stream.read()


# 6. empty files -----------------------------------------------------------


def test_empty_file_raises_csv_parse_error() -> None:
    parser = CSVParser()
    with pytest.raises(CSVParseError, match="empty"):
        list(parser.parse(io.StringIO(""), _hdfc_config()))


# 7. duplicate headers -----------------------------------------------------------


def test_duplicate_header_raises_csv_parse_error() -> None:
    duplicate_header = "Transaction Reference,Value Date,Amount,Amount,Dr/Cr,Narration"
    csv_text = f"{duplicate_header}\nREF001,15-03-2026,100.00,100.00,CR,Note\n"
    parser = CSVParser()

    with pytest.raises(CSVParseError, match="duplicate column header"):
        list(parser.parse(io.StringIO(csv_text), _hdfc_config()))


# 8. extra columns (row longer than header) --------


def test_extra_column_in_data_row_is_flagged() -> None:
    csv_text = f"{_HEADER}\nREF001,15-03-2026,100.00,CR,Acme,Note,UNEXPECTED_EXTRA\n"
    rows = _parse(csv_text)

    assert not rows[0].is_valid
    assert any(e.code == ParseErrorCode.ROW_LENGTH_MISMATCH for e in rows[0].errors)


# 9. truncated rows -------------------------


def test_truncated_row_missing_trailing_columns_is_flagged() -> None:
    csv_text = f"{_HEADER}\nREF001,15-03-2026\n"  # amount, direction, etc. all missing
    rows = _parse(csv_text)

    assert not rows[0].is_valid
    missing_fields = {
        e.field for e in rows[0].errors if e.code == ParseErrorCode.MISSING_REQUIRED_FIELD
    }
    assert "amount" in missing_fields
    assert "direction" in missing_fields


# 10. unicode characters -------------------------------


def test_unicode_characters_in_counterparty_name_are_preserved() -> None:
    csv_text = f"{_HEADER}\nREF001,15-03-2026,100.00,CR,Naïve Café Ltd — 日本語,Note\n"
    rows = _parse(csv_text)

    assert rows[0].is_valid
    assert rows[0].mapped["counterparty_name"] == "Naïve Café Ltd — 日本語"


# additional cases -----------------------------------------------------------


def test_invalid_amount_text_is_flagged() -> None:
    csv_text = f"{_HEADER}\nREF001,15-03-2026,not-a-number,CR,Acme,Note\n"
    rows = _parse(csv_text)

    assert not rows[0].is_valid
    assert any(e.code == ParseErrorCode.INVALID_AMOUNT for e in rows[0].errors)


def test_line_numbers_account_for_header_row() -> None:
    csv_text = (
        f"{_HEADER}\n"
        "REF001,15-03-2026,100.00,CR,Acme,Note\n"
        "REF002,16-03-2026,200.00,DR,Beta,Note\n"
    )
    rows = _parse(csv_text)

    assert rows[0].line_no == 2
    assert rows[1].line_no == 3


def test_amount_with_configured_thousands_and_decimal_separators() -> None:
    """Axis-style config: comma as thousands separator would need its own
    bank config test; here we exercise the same code path directly against
    an ad-hoc config to keep this test independent of any one bank file."""
    config = _hdfc_config()
    assert config.csv is not None
    custom_csv = config.csv.model_copy(
        update={"amount_thousands_separator": ",", "amount_decimal_separator": "."}
    )
    config = config.model_copy(update={"csv": custom_csv})

    csv_text = f'{_HEADER}\nREF001,15-03-2026,"1,500.00",CR,Acme,Note\n'
    rows = list(CSVParser().parse(io.StringIO(csv_text), config))

    assert rows[0].is_valid
