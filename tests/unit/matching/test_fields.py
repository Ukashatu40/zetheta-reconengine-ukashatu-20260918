# tests/unit/matching/test_fields.py
"""Tests for recon.matching.scoring.fields."""

from __future__ import annotations

from recon.matching.scoring.fields import (
    score_amount,
    score_counterparty,
    score_date,
    score_gate,
    score_reference,
)


def test_exact_reference_scores_one() -> None:
    assert score_reference("REF001", "REF001") == 1.0


def test_similar_reference_above_jaro_winkler_threshold_scores_partial() -> None:
    score = score_reference("REF0001234567", "REF0001234568")
    assert 0.92 < score < 1.0


def test_completely_different_reference_scores_zero() -> None:
    assert score_reference("REF0001234567", "ZZZZZZZZZZZZ") == 0.0


def test_long_reference_with_small_levenshtein_distance_scores_via_secondary_check() -> None:
    """A3.4: distance 1-2 for refs >=12 chars is acceptable, even if
    Jaro-Winkler itself falls short of 0.92."""
    long_ref = "ABCDEFGHIJKL"
    transposed = "ABCDEFGHIJLK"  # last two chars swapped, distance 2
    score = score_reference(long_ref, transposed)
    assert score > 0.0


def test_short_reference_does_not_get_levenshtein_leniency() -> None:
    """The Levenshtein secondary check only applies to refs >=12 chars —
    a short reference with the same edit distance gets no such leniency."""
    score = score_reference("AB", "BA")
    assert score == 0.0


def test_amount_exact_match_scores_one() -> None:
    assert score_amount(150_000, 150_000, tolerance_minor=100) == 1.0


def test_amount_within_tolerance_scores_point_eight() -> None:
    assert score_amount(150_000, 150_050, tolerance_minor=100) == 0.8


def test_amount_outside_tolerance_scores_zero() -> None:
    assert score_amount(150_000, 200_000, tolerance_minor=100) == 0.0


def test_date_same_day_scores_one() -> None:
    assert score_date(100, 100) == 1.0


def test_date_t_plus_one_scores_point_eight() -> None:
    assert score_date(100, 101) == 0.8


def test_date_t_plus_two_scores_point_five() -> None:
    assert score_date(100, 102) == 0.5


def test_date_beyond_t_plus_two_scores_zero() -> None:
    assert score_date(100, 105) == 0.0


def test_counterparty_identical_names_score_one() -> None:
    assert score_counterparty("ACME CORPORATION", "ACME CORPORATION") == 1.0


def test_counterparty_reordered_tokens_score_high_via_token_set_ratio() -> None:
    score = score_counterparty("ACME CORPORATION PRIVATE", "PRIVATE ACME CORPORATION")
    assert score > 0.9


def test_counterparty_missing_on_either_side_scores_zero() -> None:
    assert score_counterparty(None, "ACME CORPORATION") == 0.0
    assert score_counterparty("ACME CORPORATION", None) == 0.0
    assert score_counterparty(None, None) == 0.0


def test_gate_matching_values_scores_one() -> None:
    assert score_gate("INR", "INR") == 1.0


def test_gate_differing_values_scores_zero() -> None:
    assert score_gate("INR", "USD") == 0.0
