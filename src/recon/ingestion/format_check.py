# src/recon/ingestion/format_check.py
"""FORMAT_CHANGE_DETECTED (B4.1, "The Midnight Format Change").

Compares a CSV file's header row with the columns the bank's configuration
expects, before any row is read:

  missing     expected columns that are not in the file. The file cannot be
              mapped correctly, so it is rejected up front instead of producing
              45,000 quarantined rows.
  unexpected  columns the configuration does not know (ICICI's new "Tax Deducted"
              and "Net Amount"). The mapped columns still resolve by name, so the
              file is ingested, but the drift is raised as an alert.
  reordered   the expected columns appear in a different order. Harmless because
              mapping is by header name, never by position, but worth recording.

The same check backs the validation tool operations use before deploying a new
configuration.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import IO

from recon.config.models import BankConfig


@dataclass(frozen=True, slots=True)
class FormatChange:
    missing: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    reordered: bool = False
    header: list[str] = field(default_factory=list)

    @property
    def blocks_ingestion(self) -> bool:
        return bool(self.missing)

    @property
    def has_drift(self) -> bool:
        return bool(self.missing or self.unexpected or self.reordered)

    def describe(self) -> str:
        parts: list[str] = []
        if self.missing:
            parts.append(f"missing columns: {', '.join(self.missing)}")
        if self.unexpected:
            parts.append(f"new columns: {', '.join(self.unexpected)}")
        if self.reordered and not (self.missing or self.unexpected):
            parts.append("columns reordered")
        return "; ".join(parts) or "no change"


class FormatChangeDetectedError(ValueError):
    def __init__(self, bank_code: str, change: FormatChange) -> None:
        self.bank_code = bank_code
        self.change = change
        super().__init__(f"{bank_code} file format changed: {change.describe()}")


def expected_columns(config: BankConfig) -> list[str]:
    """The header names the bank's column mapping depends on, in mapping order."""
    if config.csv is None:
        return []
    return list(dict.fromkeys(config.csv.column_mapping.values()))


def compare_header(config: BankConfig, header: list[str]) -> FormatChange:
    expected = expected_columns(config)
    present = [h.strip() for h in header]
    missing = [c for c in expected if c not in present]
    unexpected = [h for h in present if h and h not in expected]
    in_file_order = [h for h in present if h in expected]
    reordered = not missing and in_file_order != expected
    return FormatChange(missing=missing, unexpected=unexpected, reordered=reordered, header=present)


def read_header(stream: IO[str], config: BankConfig) -> list[str]:
    """The first row of the stream, leaving the stream where it started."""
    if config.csv is None:
        raise ValueError(f"{config.bank_code}: no CSV configuration present")
    start = stream.tell()
    try:
        reader = csv.reader(stream, delimiter=config.csv.delimiter)
        return next(reader, [])
    finally:
        stream.seek(start)
