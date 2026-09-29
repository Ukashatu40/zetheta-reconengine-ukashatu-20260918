# src/recon/matching/orchestrator.py — replace OrchestratorOutcome and run()
"""MatchingOrchestrator (R57): sequences matching levels in order over a
single bank/source-scoped unmatched pool, and records timing and rate
metrics for the run.

AE-35: "matchable" means a transaction that received a match at ANY
level by the end of the run, not merely a transaction the exact level
happened to try. exact_match_rate_of_matchable is therefore computed
only after fuzzy has run too, since a record fuzzy later resolves must
count towards the "matchable" denominator even though exact matching
didn't touch it. The engine is not tuned to hit either rate — see
B4.4's warning that a high match rate achieved by inflating false
positives is worse than an honest lower one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from recon.config.matching_models import MatchingConfig
from recon.matching.blocking.candidates import BlockingConfig
from recon.matching.strategies.exact import ExactMatchingStrategy, ExactMatchOutcome
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
    ) -> None:
        self._session = session
        self._run_id = run_id
        self._repository = MatchingRepository(session)
        self._blocking_config = blocking_config
        self._amount_tolerance_minor = amount_tolerance_minor
        self._matching_config = matching_config

    def run(self, bank_code: str) -> OrchestratorOutcome:
        run_started = time.monotonic()

        internal_before_exact = self._repository.find_unmatched(bank_code, "INTERNAL")
        external_before_exact = self._repository.find_unmatched(bank_code, "EXTERNAL")
        total_external = len(external_before_exact)

        exact_strategy = ExactMatchingStrategy(self._session, self._run_id)
        exact_started = time.monotonic()
        exact_outcome = exact_strategy.run(internal_before_exact, external_before_exact)
        exact_duration = time.monotonic() - exact_started

        internal_before_fuzzy = self._repository.find_unmatched(bank_code, "INTERNAL")
        external_before_fuzzy = self._repository.find_unmatched(bank_code, "EXTERNAL")

        fuzzy_strategy = FuzzyMatchingStrategy(
            self._session,
            self._run_id,
            self._blocking_config,
            self._amount_tolerance_minor,
            self._matching_config,
        )
        fuzzy_started = time.monotonic()
        fuzzy_outcome = fuzzy_strategy.run(internal_before_fuzzy, external_before_fuzzy)
        fuzzy_duration = time.monotonic() - fuzzy_started

        total_matchable = (
            total_external - len(external_before_fuzzy) + fuzzy_outcome.auto_matched_count
        )
        # total_matchable = (externals exact resolved) + (externals fuzzy auto-matched).
        # Not the same as total_matched_count once PENDING_REVIEW rows exist:
        # a review-band row is claimed but not counted as matched, and per
        # AE-35 it is also not counted as "matchable" until confirmed.

        metrics = RunMetrics(
            exact_duration_seconds=exact_duration,
            fuzzy_duration_seconds=fuzzy_duration,
            total_duration_seconds=time.monotonic() - run_started,
            exact_match_rate_of_total=_safe_rate(exact_outcome.matched_count, total_external),
            exact_match_rate_of_matchable=_safe_rate(exact_outcome.matched_count, total_matchable),
            overall_match_rate_of_total=_safe_rate(
                exact_outcome.matched_count + fuzzy_outcome.auto_matched_count, total_external
            ),
        )

        return OrchestratorOutcome(
            exact=exact_outcome,
            fuzzy=fuzzy_outcome,
            internal_pool_size_before_exact=len(internal_before_exact),
            external_pool_size_before_exact=len(external_before_exact),
            internal_pool_size_before_fuzzy=len(internal_before_fuzzy),
            external_pool_size_before_fuzzy=len(external_before_fuzzy),
            metrics=metrics,
        )


def _safe_rate(numerator: int, denominator: int) -> float:
    """0.0 on an empty pool, not a ZeroDivisionError — an orchestrator run
    against zero external transactions is a real, valid case (an empty
    file, or a bank with nothing pending), not an error condition."""
    if denominator <= 0:
        return 0.0
    return numerator / denominator
