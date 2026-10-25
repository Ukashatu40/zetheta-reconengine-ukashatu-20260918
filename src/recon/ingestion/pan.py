# src/recon/ingestion/pan.py
"""Card number (PAN) detection and masking at the ingestion boundary (PCI DSS
requirement 3, Case Study C6, AE-26).

A bank file can carry a full card number in a narration or a free-text field. The
PDF places masking in the normalisation layer, but raw records are persisted
before normalisation, so a full PAN would already be stored (AE-26). Masking
therefore happens here, on every text value of a parsed row, before anything is
written or logged.

A digit run is treated as a PAN only if it passes all three checks, so that
ordinary references are not mangled: 13 to 19 digits (spaces and hyphens allowed
between groups), a recognised card network prefix, and the Luhn checksum. A masked
PAN keeps only its last four digits, e.g. ************1111.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CANDIDATE = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
_MIN_DIGITS = 13
_MAX_DIGITS = 19
_VISIBLE = 4

# Visa 4; Mastercard 51-55 and 2221-2720; Amex 34, 37; Discover 6011, 65, 644-649;
# RuPay 60, 65, 81, 82, 508; Diners 36, 38; JCB 3528-3589.
_NETWORK_PREFIX = re.compile(
    r"^(?:4|5[1-5]|2(?:2[2-9][1-9]|2[3-9]\d|[3-6]\d\d|7[01]\d|720)|3[4-8]|35(?:2[89]|[3-8]\d)"
    r"|6(?:011|5|44|45|46|47|48|49|0|8[1-2])|508|81|82)"
)


@dataclass(frozen=True, slots=True)
class MaskResult:
    text: str
    masked_count: int


def luhn_valid(digits: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2 == 1:
            value *= 2
            if value > 9:  # noqa: PLR2004
                value -= 9
        total += value
    return total % 10 == 0


def _is_pan(digits: str) -> bool:
    return (
        _MIN_DIGITS <= len(digits) <= _MAX_DIGITS
        and _NETWORK_PREFIX.match(digits) is not None
        and luhn_valid(digits)
    )


def mask_pans(text: str) -> MaskResult:
    """Replaces every PAN in text with asterisks and its last four digits."""
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        digits = re.sub(r"\D", "", match.group(0))
        if not _is_pan(digits):
            return match.group(0)
        count += 1
        return "*" * (len(digits) - _VISIBLE) + digits[-_VISIBLE:]

    masked = _CANDIDATE.sub(replace, text)
    return MaskResult(masked, count)


def mask_mapping(values: dict[str, str]) -> tuple[dict[str, str], int]:
    """Masks every string value of a mapping; returns the new mapping and how many
    PANs were masked in total."""
    total = 0
    out: dict[str, str] = {}
    for key, value in values.items():
        result = mask_pans(value)
        out[key] = result.text
        total += result.masked_count
    return out, total
