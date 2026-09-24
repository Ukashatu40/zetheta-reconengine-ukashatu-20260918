# src/recon/ingestion/parsers/csv_parser.py
"""Streaming CSV parser (R17-R20).

Uses Python's stdlib csv module (not pandas, per Day 1's explicit
instruction, for memory efficiency at scale — R19) and reads bank-specific
column mappings from the loaded BankConfig (R17/R18) rather than
hardcoding any bank's layout.

This parser does NOT enforce business rules beyond structural validity —
it extracts mapped fields as raw text and flags rows malformed at the
parsing level (missing required columns, unparsable date/amount text,
structural CSV problems). It deliberately does not reject a negative
amount: sign-vs-direction consistency is a normalisation-stage rule (see
CanonicalTransaction's validator, AE-05), not a parsing-stage one — the
two stages are kept independently testable.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import IO

from recon.config.models import BankConfig, CsvFormatConfig
from recon.ingestion.parsed_row import ParsedRow, ParseError, ParseErrorCode

_REQUIRED_MAPPED_FIELDS = ("reference", "txn_date", "amount", "direction")


class CSVParseError(ValueError):
    """Raised for file-level problems that make parsing impossible at all
    (empty file, duplicate header names). Row-level problems are captured
    as ParseError entries on individual ParsedRow objects instead — a
    malformed row must never abort ingestion of the rest of a file."""


def open_stream(path: Path, csv_config: CsvFormatConfig) -> IO[str]:
    """Open a file for CSV parsing using the bank's configured encoding.

    `newline=""` is required by the stdlib csv module's own documentation
    to handle embedded newlines in quoted fields correctly across
    platforms. A file whose actual bytes don't match csv_config.encoding
    raises UnicodeDecodeError on read, not here at open time (Python opens
    lazily) — see test_encoding_error_raised_on_read.
    """
    return path.open("r", encoding=csv_config.encoding, newline="")


class CSVParser:
    """Implements the Parser protocol for bank-specific CSV files."""

    def parse(self, stream: IO[str], config: BankConfig) -> Iterator[ParsedRow]:
        if config.csv is None:
            raise CSVParseError(f"{config.bank_code}: no CSV configuration present")
        csv_config = config.csv

        reader = csv.DictReader(stream, delimiter=csv_config.delimiter)

        fieldnames = reader.fieldnames
        if not fieldnames:
            raise CSVParseError("file is empty or has no header row")

        seen: set[str] = set()
        duplicates: set[str] = set()
        for name in fieldnames:
            if name in seen:
                duplicates.add(name)
            seen.add(name)
        if duplicates:
            raise CSVParseError(f"duplicate column header(s): {sorted(duplicates)}")

        line_no = 1  # header consumed as row 1
        for raw_row in reader:
            line_no += 1
            yield self._parse_row(line_no, raw_row, csv_config)

    def _parse_row(
        self,
        line_no: int,
        raw_row: dict[str | None, str | list[str] | None],
        csv_config: CsvFormatConfig,
    ) -> ParsedRow:
        errors: list[ParseError] = []

        if None in raw_row:
            errors.append(
                ParseError(
                    code=ParseErrorCode.ROW_LENGTH_MISMATCH,
                    field=None,
                    detail="row has more values than the header defines",
                )
            )

        raw_fields: dict[str, str] = {
            key: (value if isinstance(value, str) else "")
            for key, value in raw_row.items()
            if key is not None
        }

        mapped = self._extract_mapped_fields(raw_fields, csv_config, errors)
        self._validate_date(mapped, csv_config, errors)
        self._validate_amount(mapped, csv_config, errors)

        return ParsedRow(line_no=line_no, raw_fields=raw_fields, mapped=mapped, errors=errors)

    def _extract_mapped_fields(
        self,
        raw_fields: dict[str, str],
        csv_config: CsvFormatConfig,
        errors: list[ParseError],
    ) -> dict[str, str]:
        mapped: dict[str, str] = {}
        column_missing_fields: set[str] = set()

        for canonical_field, source_column in csv_config.column_mapping.items():
            if source_column not in raw_fields:
                column_missing_fields.add(canonical_field)
                errors.append(
                    ParseError(
                        code=ParseErrorCode.MISSING_REQUIRED_FIELD,
                        field=canonical_field,
                        detail=f"source column {source_column!r} not present in this row",
                    )
                )
                continue
            mapped[canonical_field] = raw_fields[source_column]

        for required in _REQUIRED_MAPPED_FIELDS:
            if required in column_missing_fields:
                continue  # already flagged above — avoid a duplicate error for the same field
            if not mapped.get(required, "").strip():
                errors.append(
                    ParseError(
                        code=ParseErrorCode.MISSING_REQUIRED_FIELD,
                        field=required,
                        detail="value is empty after extraction",
                    )
                )

        return mapped

    def _validate_date(
        self,
        mapped: dict[str, str],
        csv_config: CsvFormatConfig,
        errors: list[ParseError],
    ) -> None:
        txn_date = mapped.get("txn_date", "").strip()
        if not txn_date:
            return
        try:
            # Parsing here only validates that the text matches the
            # bank's configured format — the resulting datetime is
            # discarded, not stored or compared. Timezone attachment is
            # a normalisation-stage concern (recon.normalisation,
            # AE-02/AE-03's UTC-conversion rules), not a parsing-stage
            # one, so a naive datetime is correct at this layer.
            datetime.strptime(txn_date, csv_config.date_format)  # noqa: DTZ007
        except ValueError:
            errors.append(
                ParseError(
                    code=ParseErrorCode.INVALID_DATE,
                    field="txn_date",
                    detail=(
                        f"{mapped['txn_date']!r} does not match expected format "
                        f"{csv_config.date_format!r}"
                    ),
                )
            )

    def _validate_amount(
        self,
        mapped: dict[str, str],
        csv_config: CsvFormatConfig,
        errors: list[ParseError],
    ) -> None:
        amount_text = mapped.get("amount", "").strip()
        if not amount_text:
            return

        normalised_text = amount_text
        if csv_config.amount_thousands_separator:
            normalised_text = normalised_text.replace(csv_config.amount_thousands_separator, "")
        if csv_config.amount_decimal_separator != ".":
            normalised_text = normalised_text.replace(csv_config.amount_decimal_separator, ".")

        try:
            Decimal(normalised_text)
        except InvalidOperation:
            errors.append(
                ParseError(
                    code=ParseErrorCode.INVALID_AMOUNT,
                    field="amount",
                    detail=f"{amount_text!r} could not be parsed as a decimal amount",
                )
            )
