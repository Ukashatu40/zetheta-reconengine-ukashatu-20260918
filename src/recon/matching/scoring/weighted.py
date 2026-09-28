# src/recon/matching/scoring/weighted.py
"""Weighted confidence scoring per A3.4's table, with two corrections
applied (see docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md AE-08, AE-09):

AE-08 (half-open bands): score > 0.85 -> auto-match candidate;
0.60 <= score <= 0.85 -> review; score < 0.60 -> exception. Ties resolve
toward the safer bucket, per the financial-correctness principle stated
throughout the Phase 0 report.

AE-09 (minimum-evidence rule): direction and currency are HARD
CONSTRAINTS checked before scoring, not blended into the weighted sum as
ordinary evidence. A3.4's own weights would otherwise let a candidate
reach auto-match confidence on an exact reference, exact amount, and any
same-day date — zero counterparty agreement required — because direction
(0.10) and currency (0.05) contribute a constant 0.15 to every candidate
that merely passes the gate. Auto-matching additionally requires at
least two of {reference, amount, date, counterparty} to individually
clear their own field-level thresholds — not just a high blended total.

Weights, per A3.4's table:
    reference:    0.35 (threshold 0.92)
    amount:       0.25 (threshold: exact or within tolerance)
    date:         0.15 (threshold: T+3 max)
    counterparty: 0.10 (threshold 0.80)
    direction:    0.10 (hard gate, not weighted evidence)
    currency:     0.05 (hard gate, not weighted evidence)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

from recon.matching.scoring.fields import (
    COUNTERPARTY_TOKEN_SET_RATIO_THRESHOLD,
    REFERENCE_JARO_WINKLER_THRESHOLD,
    score_amount,
    score_counterparty,
    score_date,
    score_gate,
    score_reference,
)
from recon.persistence.models import NormalisedTransaction

_REFERENCE_WEIGHT = Decimal("0.35")
_AMOUNT_WEIGHT = Decimal("0.25")
_DATE_WEIGHT = Decimal("0.15")
_COUNTERPARTY_WEIGHT = Decimal("0.10")

_AUTO_MATCH_THRESHOLD = 0.85
_REVIEW_THRESHOLD = 0.60
_MIN_INDEPENDENT_SIGNALS_FOR_AUTO_MATCH = 2

_DATE_FIELD_THRESHOLD = 0.8  # T+1 or better counts as a clearing signal
_AMOUNT_FIELD_THRESHOLD = 0.8  # within tolerance or better


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
    rationale: str = field(default="")


def score_candidate(
    internal_txn: NormalisedTransaction,
    external_txn: NormalisedTransaction,
    *,
    amount_tolerance_minor: int,
) -> ScoredCandidate:
    direction_score = score_gate(internal_txn.direction, external_txn.direction)
    currency_score = score_gate(internal_txn.currency, external_txn.currency)
    hard_constraints_passed = direction_score == 1.0 and currency_score == 1.0

    reference_score = score_reference(
        internal_txn.normalised_reference or "", external_txn.normalised_reference or ""
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

    raw_confidence = (
        Decimal(str(reference_score)) * _REFERENCE_WEIGHT
        + Decimal(str(amount_score)) * _AMOUNT_WEIGHT
        + Decimal(str(date_score)) * _DATE_WEIGHT
        + Decimal(str(counterparty_score)) * _COUNTERPARTY_WEIGHT
        + Decimal(str(direction_score)) * Decimal("0.10")
        + Decimal(str(currency_score)) * Decimal("0.05")
    )
    # Quantised ONCE, here, so the value the decision is made on is
    # exactly the value later persisted to match_results.confidence
    # (NUMERIC(4,3)). Deciding on the unrounded number and storing a
    # rounded one could violate the auto_matched_requires_high_confidence
    # check constraint at the 0.85 boundary.
    confidence = float(raw_confidence.quantize(Decimal("0.001"), rounding=ROUND_HALF_EVEN))

    independent_signal_count = sum(
        [
            reference_score > REFERENCE_JARO_WINKLER_THRESHOLD or reference_score == 1.0,
            amount_score >= _AMOUNT_FIELD_THRESHOLD,
            date_score >= _DATE_FIELD_THRESHOLD,
            counterparty_score >= COUNTERPARTY_TOKEN_SET_RATIO_THRESHOLD,
        ]
    )

    decision, rationale = _decide(confidence, hard_constraints_passed, independent_signal_count)

    return ScoredCandidate(
        internal_txn=internal_txn,
        external_txn=external_txn,
        confidence=confidence,
        field_scores=field_scores,
        hard_constraints_passed=hard_constraints_passed,
        independent_signal_count=independent_signal_count,
        decision=decision,
        rationale=rationale,
    )


def _decide(
    confidence: float, hard_constraints_passed: bool, independent_signal_count: int
) -> tuple[MatchDecision, str]:
    if not hard_constraints_passed:
        return MatchDecision.NO_MATCH, "Hard constraint failed (direction or currency mismatch)."

    if confidence > _AUTO_MATCH_THRESHOLD:
        if independent_signal_count >= _MIN_INDEPENDENT_SIGNALS_FOR_AUTO_MATCH:
            return (
                MatchDecision.AUTO_MATCH,
                f"Confidence {confidence:.3f} exceeded {_AUTO_MATCH_THRESHOLD} with "
                f"{independent_signal_count} independent signals clearing their own thresholds.",
            )
        return (
            MatchDecision.REVIEW,
            f"Confidence {confidence:.3f} exceeded {_AUTO_MATCH_THRESHOLD} but only "
            f"{independent_signal_count} independent signal(s) cleared their own threshold "
            f"(AE-09: minimum {_MIN_INDEPENDENT_SIGNALS_FOR_AUTO_MATCH} required for auto-match).",
        )

    if confidence >= _REVIEW_THRESHOLD:
        return (
            MatchDecision.REVIEW,
            f"Confidence {confidence:.3f} fell in the review band "
            f"""[{_REVIEW_THRESHOLD}, {_AUTO_MATCH_THRESHOLD}]
            (AE-08: half-open, ties go to review).""",
        )

    return (
        MatchDecision.NO_MATCH,
        f"Confidence {confidence:.3f} below the review threshold {_REVIEW_THRESHOLD}.",
    )
