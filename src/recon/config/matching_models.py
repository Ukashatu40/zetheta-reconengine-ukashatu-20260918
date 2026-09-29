# src/recon/config/matching_models.py
"""Pydantic models for matching configuration (R51).

Mirrors recon.config.models's pattern: a JSON Schema catches structural
mistakes, this model catches semantic ones a schema can't express, such
as weights summing to something other than 1.0.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

WEIGHTS_SUM_TOLERANCE = 1e-9


class MatchingWeights(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference: float
    amount: float
    date: float
    counterparty: float
    direction: float
    currency: float

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> MatchingWeights:
        total = (
            self.reference
            + self.amount
            + self.date
            + self.counterparty
            + self.direction
            + self.currency
        )
        if abs(total - 1.0) > WEIGHTS_SUM_TOLERANCE:
            raise ValueError(f"weights must sum to 1.0, got {total}")
        return self


class MatchingThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference_jaro_winkler: float
    reference_levenshtein_max_distance: int
    reference_levenshtein_min_length: int
    counterparty_token_set_ratio: float
    auto_match_confidence: float
    review_confidence: float
    min_independent_signals_for_auto_match: int

    @model_validator(mode="after")
    def _review_below_auto_match(self) -> MatchingThresholds:
        if self.review_confidence >= self.auto_match_confidence:
            raise ValueError("review_confidence must be strictly less than auto_match_confidence")
        return self


class MatchingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    config_version: str
    weights: MatchingWeights
    thresholds: MatchingThresholds
