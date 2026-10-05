"""MatchingOrchestrator (R57): sequences matching levels in order over a
single bank-scoped unmatched pool, and records timing and rate metrics.

AE-35: "matchable" means a transaction that received a match at ANY level by
the end of the run. The engine is not tuned to hit either rate (B4.4).

With set_based_exact=True, a set-based SQL stage first matches the
unambiguous 1x1 key groups (DD-21); the Python exact stage then handles what
is left, including every collision group. match_result_ids on the combined
exact outcome cover only the Python stage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from recon.config.matching_models import MatchingConfig
from recon.matching.blocking.candidates import BlockingConfig
from recon.matching.strategies.exact import ExactMatchingStrategy, ExactMatchOutcome
from recon.matching.strategies.exact_set_based import SetBasedExactStage
from recon.matching.strategies.fuzzy import FuzzyMatchingStrategy, FuzzyMatchOutcome
from recon.persistence.repositories.matching import MatchingRepository


@dataclass(frozen=True, slots=True)
class RunMetrics:
    exact_duration_seconds: float
    fuzzy_duration_seconds: float
    total_duration_seconds: float
    exact_match_rate_of_total: float
    exact_match_rate_of_matchable: float
    overall_match_rate_of_total: float


@dataclass(frozen=True, slots=True)
class OrchestratorOutcome:
    exact: ExactMatchOutcome
    fuzzy: FuzzyMatchOutcome
    internal_pool_size_before_exact: int
    external_pool_size_before_exact: int
    internal_pool_size_before_fuzzy: int
    external_pool_size_before_fuzzy: int
    metrics: RunMetrics

    @property
    def total_matched_count(self) -> int:
        return self.exact.matched_count + self.fuzzy.auto_matched_count

    @property
    def total_pending_review_count(self) -> int:
        return self.fuzzy.review_count


class MatchingOrchestrator:
    def __init__(
        self,
        session: Session,
        run_id: str,
        blocking_config: BlockingConfig,
        amount_tolerance_minor: int,
        matching_config: MatchingConfig,
        *,
        set_based_exact: bool = False,
    ) -> None:
        self._session = session
        self._run_id = run_id
        self._repository = MatchingRepository(session)
        self._blocking_config = blocking_config
        self._amount_tolerance_minor = amount_tolerance_minor
        self._matching_config = matching_config
        self._set_based_exact = set_based_exact

    def run(self, bank_code: str) -> OrchestratorOutcome:
        run_started = time.monotonic()
        internal_total = self._repository.count_unmatched(bank_code, "INTERNAL")
        external_total = self._repository.count_unmatched(bank_code, "EXTERNAL")

        exact_duration = 0.0
        set_based_matched = 0
        if self._set_based_exact:
            started = time.monotonic()
            set_based_matched = SetBasedExactStage(self._session, self._run_id).run(bank_code)
            exact_duration += time.monotonic() - started

        internal_before_exact = self._repository.find_unmatched(bank_code, "INTERNAL")
        external_before_exact = self._repository.find_unmatched(bank_code, "EXTERNAL")
        started = time.monotonic()
        python_exact = ExactMatchingStrategy(self._session, self._run_id).run(
            internal_before_exact, external_before_exact
        )
        exact_duration += time.monotonic() - started
        exact_outcome = ExactMatchOutcome(
            matched_count=set_based_matched + python_exact.matched_count,
            skipped_no_key_count=python_exact.skipped_no_key_count,
            skipped_claim_conflict_count=python_exact.skipped_claim_conflict_count,
            match_result_ids=python_exact.match_result_ids,
        )

        # Re-fetch, not reuse: this is the entire mechanism behind I2.
        internal_before_fuzzy = self._repository.find_unmatched(bank_code, "INTERNAL")
        external_before_fuzzy = self._repository.find_unmatched(bank_code, "EXTERNAL")

        fuzzy_started = time.monotonic()
        fuzzy_outcome = FuzzyMatchingStrategy(
            self._session,
            self._run_id,
            self._blocking_config,
            self._amount_tolerance_minor,
            self._matching_config,
        ).run(internal_before_fuzzy, external_before_fuzzy)
        fuzzy_duration = time.monotonic() - fuzzy_started

        total_matchable = (
            external_total - len(external_before_fuzzy) + fuzzy_outcome.auto_matched_count
        )
        metrics = RunMetrics(
            exact_duration_seconds=exact_duration,
            fuzzy_duration_seconds=fuzzy_duration,
            total_duration_seconds=time.monotonic() - run_started,
            exact_match_rate_of_total=_safe_rate(exact_outcome.matched_count, external_total),
            exact_match_rate_of_matchable=_safe_rate(exact_outcome.matched_count, total_matchable),
            overall_match_rate_of_total=_safe_rate(
                exact_outcome.matched_count + fuzzy_outcome.auto_matched_count, external_total
            ),
        )
        return OrchestratorOutcome(
            exact=exact_outcome,
            fuzzy=fuzzy_outcome,
            internal_pool_size_before_exact=internal_total,
            external_pool_size_before_exact=external_total,
            internal_pool_size_before_fuzzy=len(internal_before_fuzzy),
            external_pool_size_before_fuzzy=len(external_before_fuzzy),
            metrics=metrics,
        )


def _safe_rate(numerator: int, denominator: int) -> float:
    """0.0 on an empty pool, not a ZeroDivisionError."""
    if denominator <= 0:
        return 0.0
    return numerator / denominator
