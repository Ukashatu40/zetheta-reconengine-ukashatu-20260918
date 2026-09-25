# src/recon/ingestion/parsers/mt940/lexer.py
"""MT940 lexer: splits raw MT940 text into an ordered sequence of
(tag, content) pairs.

A new field begins on any line matching ^:[0-9]{2}[A-Z]?: — every other
line is a continuation of the previous field's content. This is how
:86:'s multi-line narrative and :61:'s optional supplementary-details
continuation line are captured. The rule is structural (a line-start
pattern), never an offset into a field's content — see AE-01/AE-14 in
docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md for why fixed character offsets
are avoided everywhere in this parser.

Known scope limitation: a standalone :86: field with no immediately
preceding :61: (e.g. account-level narrative at the top or bottom of a
statement, distinct from a per-transaction narrative) is lexed correctly
but is not attached to any row by state_machine.py — it is currently
dropped rather than persisted or raised as an error. None of the D10
sample files require this; documented here rather than silently ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TAG_LINE = re.compile(r"^:(\d{2}[A-Z]?):(.*)$")


@dataclass(frozen=True, slots=True)
class LexedField:
    tag: str
    content: str
    line_no: int  # 1-based line number of the field's opening line


class MT940LexError(ValueError):
    """Raised for structural problems that make lexing impossible at all —
    content before any tag, or an entirely empty stream. Mirrors
    CSVParseError's role in the CSV parser: file-level, not row-level."""


def lex(text: str) -> list[LexedField]:
    lines = text.splitlines()
    fields: list[LexedField] = []
    current_tag: str | None = None
    current_content: list[str] = []
    current_line_no = 0

    def _flush() -> None:
        if current_tag is not None:
            fields.append(
                LexedField(
                    tag=current_tag,
                    content="\n".join(current_content),
                    line_no=current_line_no,
                )
            )

    for i, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        match = _TAG_LINE.match(line)
        if match:
            _flush()
            current_tag = match.group(1)
            current_content = [match.group(2)]
            current_line_no = i
        else:
            if current_tag is None:
                raise MT940LexError(f"line {i}: content before any tag: {line!r}")
            current_content.append(line)

    _flush()

    if not fields:
        raise MT940LexError("file is empty or contains no recognisable MT940 fields")

    return fields
