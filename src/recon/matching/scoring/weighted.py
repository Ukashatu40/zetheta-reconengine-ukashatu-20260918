# src/recon/matching/scoring/weighted.py
"""Weighted confidence scoring per A3.4's table, with two corrections
(AE-08, AE-09). Weights and thresholds come from a MatchingConfig
(R51), not module constants — see config/matching/weights.yaml.

AE-08: score > auto_match_confidence -> AUTO_MATCH; review_confidence
<= score <= auto_match_confidence -> REVIEW; below that -> NO_MATCH.

AE-09: direction and currency are hard constraints checked before
scoring, not blended evidence. Auto-match additionally requires at least
min_independent_signals_for_auto_match of {reference, amount, date,
counterparty} to individually clear their own field threshold. See
AE-09's addendum in docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md: with
A3.4's stated weights this branch is unreachable through realistic
scoring and is proven directly against _decide().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

from recon.config.matching_models import MatchingConfig, MatchingThresholds
from recon.matching.scoring.fields import (
    differs_only_in_digits,
    score_amount,
    score_counterparty,
    score_date,
    score_gate,
    score_reference,
)
from recon.persistence.models import NormalisedTransaction


class MatchDecision(StrEnum):
    AUTO_MATCH = "AUTO_MATCH"
    REVIEW = "REVIEW"
    NO_MATCH = "NO_MATCH"


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    internal_txn: NormalisedTransaction
    external_txn: NormalisedTransaction
    confidence: float
    field_scores: dict[str, float]
    hard_constraints_passed: bool
    independent_signal_count: int
    decision: MatchDecision
    weights_version: str
    rationale: str = field(default="")


def score_candidate(
    internal_txn: NormalisedTransaction,
    external_txn: NormalisedTransaction,
    *,
    amount_tolerance_minor: int,
    config: MatchingConfig,
) -> ScoredCandidate:
    direction_score = score_gate(internal_txn.direction, external_txn.direction)
    currency_score = score_gate(internal_txn.currency, external_txn.currency)
    hard_constraints_passed = direction_score == 1.0 and currency_score == 1.0

    reference_score = score_reference(
        internal_txn.normalised_reference or "",
        external_txn.normalised_reference or "",
        jaro_winkler_threshold=config.thresholds.reference_jaro_winkler,
        levenshtein_max_distance=config.thresholds.reference_levenshtein_max_distance,
        levenshtein_min_length=config.thresholds.reference_levenshtein_min_length,
    )
    amount_score = score_amount(
        internal_txn.amount_minor, external_txn.amount_minor, amount_tolerance_minor
    )
    date_score = score_date(internal_txn.txn_date.toordinal(), external_txn.txn_date.toordinal())
    counterparty_score = score_counterparty(
        internal_txn.counterparty_name_normalised, external_txn.counterparty_name_normalised
    )

    field_scores = {
        "reference": reference_score,
        "amount": amount_score,
        "date": date_score,
        "counterparty": counterparty_score,
        "direction": direction_score,
        "currency": currency_score,
    }

    w = config.weights
    raw_confidence = (
        Decimal(str(reference_score)) * Decimal(str(w.reference))
        + Decimal(str(amount_score)) * Decimal(str(w.amount))
        + Decimal(str(date_score)) * Decimal(str(w.date))
        + Decimal(str(counterparty_score)) * Decimal(str(w.counterparty))
        + Decimal(str(direction_score)) * Decimal(str(w.direction))
        + Decimal(str(currency_score)) * Decimal(str(w.currency))
    )
    # Quantised once, here, so the value the decision is made on equals
    # the value later persisted (match_results.confidence, NUMERIC(4,3)).
    # See IB-04.
    confidence = float(raw_confidence.quantize(Decimal("0.001"), rounding=ROUND_HALF_EVEN))

    t = config.thresholds
    independent_signal_count = sum(
        [
            reference_score > t.reference_jaro_winkler or reference_score == 1.0,
            amount_score
            >= 0.8,  # noqa: PLR2004 -- "within tolerance or better", not itself configurable per A3.4's fixed scoring bands
            date_score >= 0.8,  # noqa: PLR2004 -- "T+1 or better", same reasoning
            counterparty_score >= t.counterparty_token_set_ratio,
        ]
    )

    uncorroborated_digit_variance = (
        differs_only_in_digits(
            internal_txn.normalised_reference or "", external_txn.normalised_reference or ""
        )
        and counterparty_score < t.counterparty_token_set_ratio
    )
    decision, rationale = _decide(
        confidence,
        hard_constraints_passed,
        independent_signal_count,
        t,
        uncorroborated_digit_variance=uncorroborated_digit_variance,
    )

    return ScoredCandidate(
        internal_txn=internal_txn,
        external_txn=external_txn,
        confidence=confidence,
        field_scores=field_scores,
        hard_constraints_passed=hard_constraints_passed,
        independent_signal_count=independent_signal_count,
        decision=decision,
        weights_version=config.config_version,
        rationale=rationale,
    )


def _decide(
    confidence: float,
    hard_constraints_passed: bool,
    independent_signal_count: int,
    thresholds: MatchingThresholds,
    *,
    uncorroborated_digit_variance: bool = False,
) -> tuple[MatchDecision, str]:
    if not hard_constraints_passed:
        return MatchDecision.NO_MATCH, "Hard constraint failed (direction or currency mismatch)."

    if confidence > thresholds.auto_match_confidence:
        if independent_signal_count < thresholds.min_independent_signals_for_auto_match:
            return (
                MatchDecision.REVIEW,
                f"Confidence {confidence:.3f} exceeded {thresholds.auto_match_confidence} but only "
                f"{independent_signal_count} independent signal(s) cleared their own threshold "
                f"(AE-09: minimum {thresholds.min_independent_signals_for_auto_match} required).",
            )
        if uncorroborated_digit_variance:
            return (
                MatchDecision.REVIEW,
                f"Confidence {confidence:.3f} exceeded {thresholds.auto_match_confidence} but the "
                "references differ only in digits and no counterparty corroborates; sequential "
                "identifiers cannot be told from typos (AE-37).",
            )
        return (
            MatchDecision.AUTO_MATCH,
            f"Confidence {confidence:.3f} exceeded {thresholds.auto_match_confidence} with "
            f"{independent_signal_count} independent signals clearing their own thresholds.",
        )

    if confidence >= thresholds.review_confidence:
        return (
            MatchDecision.REVIEW,
            f"Confidence {confidence:.3f} fell in the review band "
            f"[{thresholds.review_confidence}, {thresholds.auto_match_confidence}] "
            "(AE-08: half-open, ties go to review).",
        )

    return (
        MatchDecision.NO_MATCH,
        f"Confidence {confidence:.3f} below the review threshold {thresholds.review_confidence}.",
    )
