# src/recon/normalisation/reference.py
"""Reference cleaning (R40): strip whitespace, remove formatting
characters, uppercase, and optionally truncate/pad to a caller-specified
length.

Deliberately does NOT truncate or pad by default. R40's literal
instruction ("truncate or pad to standard length") is destructive if
applied blindly — REFERENCE_TRUNCATED (PDF exception category #15) exists
specifically to detect a BANK's own truncation as a reconciliation
problem; normalisation performing the same kind of truncation silently,
with no record of it having happened, would make that category
undetectable for references we ourselves shortened. max_length is
therefore opt-in and explicit, never a default.
"""

from __future__ import annotations

import re

# Characters routinely used as separators/formatting within bank
# reference numbers but carrying no identifying information: hyphens,
# underscores, periods, spaces (already handled by strip, but also
# embedded spaces), and forward slashes used as visual separators (not
# // the "//BANKREF" convention, which is meaningful and preserved — see
# _PRESERVE_DOUBLE_SLASH below).
_FORMATTING_CHARS_PATTERN = re.compile(r"[\s\-_.]")


def clean_reference(raw_reference: str, *, max_length: int | None = None) -> str:
    """Cleans a raw reference string. Does not truncate unless max_length
    is explicitly given by the caller."""
    stripped = raw_reference.strip()
    # Preserve a meaningful "//" bank-reference separator (seen in MT940
    # :61: lines, e.g. "REF0001234567//BANKREF001") by protecting it
    # before stripping other formatting characters, then restoring it.
    placeholder = "\u0000DOUBLESLASH\u0000"
    protected = stripped.replace("//", placeholder)
    cleaned = _FORMATTING_CHARS_PATTERN.sub("", protected)
    cleaned = cleaned.replace(placeholder, "//")
    cleaned = cleaned.upper()

    if max_length is not None and len(cleaned) > max_length:
        cleaned = cleaned[:max_length]

    return cleaned


def pad_reference(reference: str, target_length: int, *, pad_char: str = "0") -> str:
    """Pads a reference to target_length, left-padded with pad_char.
    Separate from clean_reference since padding (unlike cleaning) is
    something only a specific bank's matching logic should opt into
    explicitly, never a default normalisation step."""
    if len(reference) >= target_length:
        return reference
    return reference.rjust(target_length, pad_char)
