# src/recon/matching/scoring/fields.py
"""Per-field similarity scoring, implementing A3.4's scoring methods.
Thresholds are parameters (R51: configurable), not module constants —
see recon.config.matching_models.MatchingThresholds and
config/matching/weights.yaml for the defaults, which match A3.4 exactly.

R49: RapidFuzz's fuzz.token_set_ratio and distance.JaroWinkler are used
directly — no phonetic algorithms (DD-05).
"""

from __future__ import annotations

from rapidfuzz.distance import JaroWinkler, Levenshtein
from rapidfuzz.fuzz import token_set_ratio

_SAME_DAY = 0
_T_PLUS_ONE = 1
_T_PLUS_TWO = 2


def score_reference(
    internal_ref: str,
    external_ref: str,
    *,
    jaro_winkler_threshold: float,
    levenshtein_max_distance: int,
    levenshtein_min_length: int,
) -> float:
    """Exact match scores 1.0. Otherwise Jaro-Winkler is used if it
    clears jaro_winkler_threshold; below that, a Levenshtein-distance
    check is tried for references at least levenshtein_min_length long
    (A3.4: "distance of 1-2 with a string length of 12+ characters is
    typically acceptable"). Neither clearing scores 0.0."""
    if internal_ref == external_ref:
        return 1.0

    jaro_winkler_score = JaroWinkler.similarity(internal_ref, external_ref)
    if jaro_winkler_score > jaro_winkler_threshold:
        return jaro_winkler_score

    if len(internal_ref) >= levenshtein_min_length:
        distance = Levenshtein.distance(internal_ref, external_ref)
        if distance <= levenshtein_max_distance:
            return 1.0 - (distance / (levenshtein_max_distance + 1))

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
    """A3.4: Token Set Ratio / 100."""
    if not internal_name or not external_name:
        return 0.0
    return token_set_ratio(internal_name, external_name) / 100.0


def score_gate(internal_value: str, external_value: str) -> float:
    """A3.4's direction/currency scoring: exact = 1.0, else 0 — used as a
    hard constraint (see scoring/weighted.py), not blended evidence."""
    return 1.0 if internal_value == external_value else 0.0
