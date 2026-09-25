# src/recon/ingestion/parsed_row.py
"""ParsedRow: the output of every format-specific parser, before
normalisation.

Deliberately NOT the canonical model. A ParsedRow carries source values as
text, exactly as extracted, plus a list of validation problems found
during parsing. Normalisation (recon.normalisation, WP3) is what turns a
valid ParsedRow into a CanonicalTransaction — parsing and normalising stay
separate stages per the PDF's own four-stage pipeline (A1.2), so each can
be tested independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ParseErrorCode(StrEnum):
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_DATE = "INVALID_DATE"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    DUPLICATE_HEADER = "DUPLICATE_HEADER"
    ENCODING_ERROR = "ENCODING_ERROR"
    ROW_LENGTH_MISMATCH = "ROW_LENGTH_MISMATCH"
    MALFORMED_FIELD = "MALFORMED_FIELD"


@dataclass(frozen=True, slots=True)
class ParseError:
    code: ParseErrorCode
    field: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class ParsedRow:
    line_no: int
    raw_fields: dict[str, str]
    mapped: dict[str, str]
    errors: list[ParseError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0
