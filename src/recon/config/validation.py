# src/recon/config/validation.py
"""The column-mapping validation tool operations use before deploying a new bank
configuration (B4.1, "The Midnight Format Change").

Given a candidate configuration and, optionally, a sample of the bank's file, it
reports whether the configuration is valid, whether the sample's header matches
the mapping (missing, new and reordered columns), and how the first rows parse
(valid, malformed, and a preview of what each mapped field would hold). Nothing is
stored and nothing goes live.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import IO

from recon.config.loader import BankConfigError, parse_bank_config
from recon.config.models import BankConfig
from recon.ingestion.format_check import FormatChange, compare_header, read_header
from recon.ingestion.parsers.csv_parser import CSVParseError, CSVParser

DEFAULT_SAMPLE_ROWS = 25
_PREVIEW_ROWS = 5


@dataclass(frozen=True, slots=True)
class SampleReport:
    header: list[str]
    change: FormatChange
    rows_checked: int
    rows_valid: int
    errors: list[str]
    preview: list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class ValidationReport:
    ok: bool
    config_errors: list[str] = field(default_factory=list)
    bank_code: str | None = None
    config_version: str | None = None
    sample: SampleReport | None = None
    warnings: list[str] = field(default_factory=list)


def validate_sample(
    config: BankConfig, stream: IO[str], max_rows: int = DEFAULT_SAMPLE_ROWS
) -> SampleReport:
    header = read_header(stream, config)
    change = compare_header(config, header)
    if change.blocks_ingestion:
        return SampleReport(header, change, 0, 0, [change.describe()], [])

    parsed = []
    errors: list[str] = []
    try:
        for row in CSVParser().parse(stream, config):
            parsed.append(row)
            if len(parsed) >= max_rows:
                break
    except CSVParseError as exc:
        return SampleReport(header, change, 0, 0, [str(exc)], [])
    for row in parsed:
        for error in row.errors:
            errors.append(
                f"line {row.line_no}: {error.code} {error.field or ''} {error.detail}".strip()
            )
    return SampleReport(
        header=header,
        change=change,
        rows_checked=len(parsed),
        rows_valid=sum(1 for r in parsed if r.is_valid),
        errors=errors[:50],
        preview=[dict(r.mapped) for r in parsed[:_PREVIEW_ROWS]],
    )


def validate_candidate(
    config_text: str, source: str, sample_text: str | None = None
) -> ValidationReport:
    try:
        config = parse_bank_config(config_text, source)
    except BankConfigError as exc:
        return ValidationReport(ok=False, config_errors=[str(exc)])

    warnings: list[str] = []
    if sample_text is None:
        return ValidationReport(
            ok=True,
            bank_code=config.bank_code,
            config_version=config.config_version,
            warnings=["no sample file was given; only the configuration itself was checked"],
        )
    if config.csv is None:
        warnings.append("this configuration has no CSV section, so the sample was not checked")
        return ValidationReport(
            ok=True,
            bank_code=config.bank_code,
            config_version=config.config_version,
            warnings=warnings,
        )
    sample = validate_sample(config, io.StringIO(sample_text, newline=""))
    if sample.change.unexpected:
        warnings.append(
            "the sample has columns the mapping does not use: "
            + ", ".join(sample.change.unexpected)
        )
    ok = (
        not sample.change.blocks_ingestion
        and sample.rows_checked > 0
        and sample.rows_valid == sample.rows_checked
    )
    return ValidationReport(
        ok=ok,
        bank_code=config.bank_code,
        config_version=config.config_version,
        sample=sample,
        warnings=warnings,
    )
