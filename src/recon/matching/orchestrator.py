# src/recon/matching/orchestrator.py
"""MatchingOrchestrator (R57): sequences matching levels in order over a
single bank/source-scoped unmatched pool.

The monotonic-strength invariant (I2 — "a weaker match never overwrites a
stronger one") is not implemented as a special check here. It falls out
of two properties already in place elsewhere in this codebase:

  1. Each level re-fetches the CURRENT unmatched pool immediately before
     running, rather than reusing a pool computed before an earlier level
     ran. A transaction claimed by exact matching is never handed to
     fuzzy matching at all, because it no longer appears in the
     unmatched query's results once its match_status flips to MATCHED.

  2. ClaimsService's unique partial index (WP4 Increment 1) makes a
     second active claim on an already-matched transaction a database-
     level impossibility, closing the race a naive re-fetch alone
     wouldn't fully guarantee under concurrent execution.

This module's job is therefore narrow: call MatchingRepository between
each level, in the right order, and aggregate what each level reports.
The proof that this actually holds is a test, not a runtime assertion —
see tests/integration/test_orchestrator.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from recon.config.matching_models import MatchingConfig
from recon.matching.blocking.candidates import BlockingConfig
from recon.matching.strategies.exact import ExactMatchingStrategy, ExactMatchOutcome
from recon.matching.strategies.fuzzy import FuzzyMatchingStrategy, FuzzyMatchOutcome
from recon.persistence.repositories.matching import MatchingRepository


@dataclass(frozen=True, slots=True)
class OrchestratorOutcome:
    """A plain, in-memory summary of one orchestration run. NOT the
    persisted reconciliation_runs table (which does not exist yet — see
    this increment's module-level design note) — this is what the caller
    receives back directly, nothing more."""

    exact: ExactMatchOutcome
    fuzzy: FuzzyMatchOutcome
    internal_pool_size_before_exact: int
    external_pool_size_before_exact: int
    internal_pool_size_before_fuzzy: int
    external_pool_size_before_fuzzy: int

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
        internal_before_exact = self._repository.find_unmatched(bank_code, "INTERNAL")
        external_before_exact = self._repository.find_unmatched(bank_code, "EXTERNAL")

        exact_strategy = ExactMatchingStrategy(self._session, self._run_id)
        exact_outcome = exact_strategy.run(internal_before_exact, external_before_exact)

        # Re-fetch, not reuse: this is the entire mechanism behind I2.
        # Anything exact_strategy matched is now MATCHED and will not
        # appear in these results at all.
        internal_before_fuzzy = self._repository.find_unmatched(bank_code, "INTERNAL")
        external_before_fuzzy = self._repository.find_unmatched(bank_code, "EXTERNAL")

        fuzzy_strategy = FuzzyMatchingStrategy(
            self._session,
            self._run_id,
            self._blocking_config,
            self._amount_tolerance_minor,
            self._matching_config,
        )
        fuzzy_outcome = fuzzy_strategy.run(internal_before_fuzzy, external_before_fuzzy)

        return OrchestratorOutcome(
            exact=exact_outcome,
            fuzzy=fuzzy_outcome,
            internal_pool_size_before_exact=len(internal_before_exact),
            external_pool_size_before_exact=len(external_before_exact),
            internal_pool_size_before_fuzzy=len(internal_before_fuzzy),
            external_pool_size_before_fuzzy=len(external_before_fuzzy),
        )
