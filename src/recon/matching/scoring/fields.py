# src/recon/matching/scoring/fields.py
"""Per-field similarity scoring, implementing A3.4's scoring methods
exactly as specified per field — Jaro-Winkler for reference,
Levenshtein-distance tolerance for reference (used as a secondary check,
not a replacement), Token Set Ratio for counterparty, exact-or-nothing
for amount/date/direction/currency banding.

R49: RapidFuzz's fuzz.token_set_ratio and distance.JaroWinkler are used
directly — no phonetic algorithms (see recon.normalisation.counterparty's
scope decision, carried forward here).
"""

from __future__ import annotations

from rapidfuzz.distance import JaroWinkler, Levenshtein
from rapidfuzz.fuzz import token_set_ratio

REFERENCE_JARO_WINKLER_THRESHOLD = 0.92
REFERENCE_LEVENSHTEIN_MAX_DISTANCE = 2
REFERENCE_LEVENSHTEIN_MIN_LENGTH = 12
COUNTERPARTY_TOKEN_SET_RATIO_THRESHOLD = 0.80


def score_reference(internal_ref: str, external_ref: str) -> float:
    """Exact match scores 1.0. Otherwise, Jaro-Winkler similarity is used
    if it clears the configured threshold; below that, a Levenshtein-
    distance check is tried as a secondary signal specifically for longer
    references (A3.4: "a Levenshtein distance of 1-2 with a string length
    of 12+ characters is typically acceptable") — this catches transposed-
    digit typos that Jaro-Winkler's prefix-weighting can under-score.
    Neither clearing its threshold scores 0.0, per A3.4's own "else 0".
    """
    if internal_ref == external_ref:
        return 1.0

    jaro_winkler_score = JaroWinkler.similarity(internal_ref, external_ref)
    if jaro_winkler_score > REFERENCE_JARO_WINKLER_THRESHOLD:
        return jaro_winkler_score

    if len(internal_ref) >= REFERENCE_LEVENSHTEIN_MIN_LENGTH:
        distance = Levenshtein.distance(internal_ref, external_ref)
        if distance <= REFERENCE_LEVENSHTEIN_MAX_DISTANCE:
            # Scored proportionally to distance rather than a flat 1.0 —
            # a distance-1 typo is stronger evidence than a distance-2 one.
            return 1.0 - (distance / (REFERENCE_LEVENSHTEIN_MAX_DISTANCE + 1))

    return 0.0


def score_amount(
    internal_amount_minor: int, external_amount_minor: int, tolerance_minor: int
) -> float:
    """A3.4: exact = 1.0, within configured tolerance = 0.8, else 0."""
    if internal_amount_minor == external_amount_minor:
        return 1.0
    if abs(internal_amount_minor - external_amount_minor) <= tolerance_minor:
        return 0.8
    return 0.0


_SAME_DAY = 0
_T_PLUS_ONE = 1
_T_PLUS_TWO = 2


def score_date(internal_txn_date_ordinal: int, external_txn_date_ordinal: int) -> float:
    """A3.4: same date = 1.0, T+1 = 0.8, T+2 = 0.5, else 0 (T+3 max)."""
    offset = abs(internal_txn_date_ordinal - external_txn_date_ordinal)
    if offset == _SAME_DAY:
        return 1.0
    if offset == _T_PLUS_ONE:
        return 0.8
    if offset == _T_PLUS_TWO:
        return 0.5
    return 0.0


def score_counterparty(internal_name: str | None, external_name: str | None) -> float:
    """A3.4: Token Set Ratio / 100. Returns 0.0 (not None, not an
    exception) when either side lacks a counterparty name at all — a
    common, expected case for MT940 sources (AE-01) — rather than raising
    or silently treating absence as agreement."""
    if not internal_name or not external_name:
        return 0.0
    return token_set_ratio(internal_name, external_name) / 100.0


def score_gate(internal_value: str, external_value: str) -> float:
    """A3.4's direction/currency scoring: exact = 1.0, else 0 — used as a
    HARD CONSTRAINT (see recon.matching.scoring.weighted), not blended
    evidence. See AE-09 for why treating these as ordinary weighted
    evidence would let them inflate every surviving candidate by a
    constant amount regardless of genuine similarity elsewhere.
    """
    return 1.0 if internal_value == external_value else 0.0
