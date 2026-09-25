# src/recon/ingestion/parsers/mt940/field_61.py
"""Structural parser for the MT940 :61: statement line.

AE-14: extraction is entirely regex/character-class based — value date,
entry date, mark, funds code, amount and transaction-type boundaries are
all determined by digit-vs-alpha transitions, never by fixed byte
offsets. This is what lets one parser handle multiple bank-specific
deviations without a different offset table per bank.

The funds-code sub-field genuinely needs no configuration to detect
correctly: it is alphabetic and the amount that follows is numeric, so
its presence or absence is unambiguous from the character class alone.
This is the actual resolution of the "missing funds code" deviation
(D2) — not a config flag, a property of the grammar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FIELD_61_PATTERN = re.compile(
    r"^(?P<value_date>\d{6})"
    r"(?P<entry_date>\d{4})?"
    r"(?P<mark>RC|RD|C|D)"
    r"(?P<funds_code>[A-Za-z])?"
    r"(?P<amount>[\d.,]+)"
    r"(?P<type_code>[A-Za-z][A-Za-z0-9]{3})"
    r"(?P<reference>.*)$"
)


class Field61ParseError(ValueError):
    """Raised when a :61: field's content does not match the expected
    structure at all. Caught by state_machine.py and turned into a
    MALFORMED_FIELD error on that row — a single bad line never aborts
    the rest of the file."""


@dataclass(frozen=True, slots=True)
class ParsedField61:
    value_date_raw: str  # YYMMDD, unconverted — date interpretation is a normalisation concern
    entry_date_raw: str | None  # MMDD, optional
    mark: str  # one of C, D, RC, RD
    is_reversal: bool
    funds_code: str | None
    amount_raw: str  # exactly as it appeared, before decimal-separator normalisation
    transaction_type_code: str
    reference_raw: str  # everything after the type code, including any //bank-ref suffix
    supplementary_details: str | None  # continuation line, if the field had one


def parse_field_61(content: str) -> ParsedField61:
    lines = content.split("\n", 1)
    first_line = lines[0]
    supplementary = lines[1].strip() if len(lines) > 1 and lines[1].strip() else None

    match = _FIELD_61_PATTERN.match(first_line)
    if not match:
        raise Field61ParseError(f":61: content does not match expected structure: {first_line!r}")

    mark = match.group("mark")
    return ParsedField61(
        value_date_raw=match.group("value_date"),
        entry_date_raw=match.group("entry_date"),
        mark=mark,
        is_reversal=mark in ("RC", "RD"),
        funds_code=match.group("funds_code"),
        amount_raw=match.group("amount"),
        transaction_type_code=match.group("type_code"),
        reference_raw=match.group("reference").strip(),
        supplementary_details=supplementary,
    )
