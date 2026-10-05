# src/recon/bench/evaluate.py
"""Scores match outcomes against the generator's ground truth. Pure: no database."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from recon.bench.dataset import SyntheticRecord


@dataclass(frozen=True, slots=True)
class MatchOutcome:
    internal_id: uuid.UUID
    external_id: uuid.UUID
    status: str  # AUTO_MATCHED or PENDING_REVIEW
    match_type: str


@dataclass(frozen=True, slots=True)
class Evaluation:
    internal_count: int
    external_count: int
    true_pairs: int
    auto_total: int
    auto_correct: int
    review_total: int
    review_correct: int
    exact_correct: int
    per_scenario: dict[str, dict[str, int]]

    @property
    def false_auto_matches(self) -> int:
        return self.auto_total - self.auto_correct

    @property
    def auto_precision(self) -> float | None:
        return self.auto_correct / self.auto_total if self.auto_total else None

    @property
    def auto_recall_of_true_pairs(self) -> float | None:
        return self.auto_correct / self.true_pairs if self.true_pairs else None

    @property
    def auto_rate_of_externals(self) -> float | None:
        return self.auto_total / self.external_count if self.external_count else None

    @property
    def exact_rate_of_externals(self) -> float | None:
        return self.exact_correct / self.external_count if self.external_count else None

    @property
    def exact_rate_of_true_pairs(self) -> float | None:
        return self.exact_correct / self.true_pairs if self.true_pairs else None


def evaluate(records: Sequence[SyntheticRecord], outcomes: Sequence[MatchOutcome]) -> Evaluation:
    pair_key = {r.id: r.pair_key for r in records}

    def is_correct(outcome: MatchOutcome) -> bool:
        key = pair_key.get(outcome.internal_id)
        return key is not None and key == pair_key.get(outcome.external_id)

    auto = [o for o in outcomes if o.status == "AUTO_MATCHED"]
    review = [o for o in outcomes if o.status == "PENDING_REVIEW"]

    by_record: dict[uuid.UUID, MatchOutcome] = {}
    for outcome in outcomes:
        by_record[outcome.internal_id] = outcome
        by_record[outcome.external_id] = outcome

    # One primary record per entity: the internal side when there is one.
    primary: dict[int, SyntheticRecord] = {}
    for record in records:
        current = primary.get(record.entity_id)
        if current is None or (record.source == "INTERNAL" and current.source != "INTERNAL"):
            primary[record.entity_id] = record

    per_scenario: dict[str, Counter[str]] = {}
    for record in primary.values():
        # Using a distinct variable name prevents scope pollution and type inference collision
        record_match = by_record.get(record.id)
        if record_match is None:
            label = "unmatched"
        else:
            kind = "auto" if record_match.status == "AUTO_MATCHED" else "review"
            label = f"{kind}_{'correct' if is_correct(record_match) else 'wrong'}"
        per_scenario.setdefault(record.scenario, Counter())[label] += 1

    return Evaluation(
        internal_count=sum(1 for r in records if r.source == "INTERNAL"),
        external_count=sum(1 for r in records if r.source == "EXTERNAL"),
        true_pairs=sum(1 for r in primary.values() if r.pair_key is not None),
        auto_total=len(auto),
        auto_correct=sum(1 for o in auto if is_correct(o)),
        review_total=len(review),
        review_correct=sum(1 for o in review if is_correct(o)),
        exact_correct=sum(1 for o in auto if o.match_type == "EXACT" and is_correct(o)),
        per_scenario={name: dict(counts) for name, counts in per_scenario.items()},
    )
