# src/recon/normalisation/counterparty.py
"""Counterparty name cleaning (R42): case normalisation, common
abbreviation expansion, and special-character removal.

Scope decision (see docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md): this
module does NOT attempt phonetic normalisation or transliteration-variant
matching. A3.2 suggests Soundex/Double Metaphone for this; Soundex is
English-phonetics-specific and a poor fit for Indian-name transliteration
variance, and RapidFuzz (the PDF's own mandated fuzzy-matching library)
ships no phonetic algorithms at all. Tolerance for name variance (e.g.
"Acme Corp" vs "ACME CORPORATION") is deliberately left to the fuzzy
MATCHING engine (WP4, Token Set Ratio) operating on the cleaned-but-not-
phonetically-collapsed output of this module, not built into
normalisation itself.
"""

from __future__ import annotations

import re

# A small, explicit, extensible table — not an attempt at completeness.
# Extending this table is safe (it only ever expands a form into a more
# complete one); it never merges two DIFFERENT entities into one name,
# which is the property that keeps this a normalisation step rather than
# an identity-resolution step.
_ABBREVIATION_EXPANSIONS: dict[str, str] = {
    "PVT": "PRIVATE",
    "PVT.": "PRIVATE",
    "LTD": "LIMITED",
    "LTD.": "LIMITED",
    "CORP": "CORPORATION",
    "CORP.": "CORPORATION",
    "CO": "COMPANY",
    "CO.": "COMPANY",
    "INC": "INCORPORATED",
    "INC.": "INCORPORATED",
    "LLP": "LIMITED LIABILITY PARTNERSHIP",
}

# Characters stripped entirely: punctuation with no identifying value.
# Does not strip letters, digits, or spaces.
_SPECIAL_CHARS_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)


def register_abbreviation(abbreviation: str, expansion: str) -> None:
    """Extends the abbreviation table at runtime — mirrors
    recon.domain.money.register_currency_exponent's pattern for
    extensibility without editing this module directly."""
    _ABBREVIATION_EXPANSIONS[abbreviation.strip().upper()] = expansion.strip().upper()


def clean_counterparty_name(raw_name: str) -> str:
    """Uppercases, strips special characters, collapses whitespace, and
    expands known abbreviations token-by-token."""
    stripped = raw_name.strip().upper()
    without_special_chars = _SPECIAL_CHARS_PATTERN.sub(" ", stripped)
    collapsed = re.sub(r"\s+", " ", without_special_chars).strip()

    tokens = collapsed.split(" ")
    expanded_tokens = [_ABBREVIATION_EXPANSIONS.get(token, token) for token in tokens]

    return " ".join(expanded_tokens)
