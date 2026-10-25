"""MatchingOrchestrator (R57): sequences matching levels in order over a
single bank-scoped unmatched pool, and records timing and rate metrics.

AE-35: "matchable" means a transaction that received a match at ANY level by
the end of the run. The engine is not tuned to hit either rate (B4.4).

With set_based_exact=True, a set-based SQL stage first matches the
unambiguous 1x1 key groups (DD-21); the Python exact stage then handles what
is left, including every collision group. match_result_ids on the combined
exact outcome cover only the Python stage.

Stage order with a RuleStageConfig: exact, then cross-currency and split/netted
groups, then fuzzy, then the one-to-one rules. Groups run BEFORE fuzzy on purpose:
fuzzy scores one part of a split settlement (similar reference, wrong amount) at
about 0.65, which lands in the review band and claims the internal transaction,
making the group unreachable. A unique, linked, exact-sum group is stronger
evidence than that, so it goes first. Each stage works on the pool the previous
one left, so a weaker match never overrides a stronger one (I2).

mode="FAST" runs the exact stage only (B4.3's backlog fast mode): fuzzy
matching is skipped and the unmatched pool is left for a later FULL run.
date_from/date_to limit every pool to an inclusive txn_date range.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from recon.audit.logger import AuditLogger
from recon.config.matching_models import MatchingConfig
from recon.fx.service import FxService
from recon.matching.blocking.candidates import BlockingConfig
from recon.matching.stage_config import RuleStageConfig
from recon.matching.strategies.cross_currency import CrossCurrencyOutcome, CrossCurrencyStrategy
from recon.matching.strategies.exact import ExactMatchingStrategy, ExactMatchOutcome
from recon.matching.strategies.exact_set_based import SetBasedExactStage
from recon.matching.strategies.fuzzy import FuzzyMatchingStrategy, FuzzyMatchOutcome
from recon.matching.strategies.group import GroupMatchingStrategy, GroupMatchOutcome
from recon.matching.strategies.rule_based import RuleBasedMatchingStrategy, RuleMatchOutcome
from recon.persistence.models import NormalisedTransaction
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
    rule: RuleMatchOutcome | None = None
    cross_currency: CrossCurrencyOutcome | None = None
    group: GroupMatchOutcome | None = None
    rule_duration_seconds: float = 0.0
    externals_matched: int = field(default=0)

    @property
    def total_matched_count(self) -> int:
        """Auto-matched pairs and groups, counting a group once."""
        return (
            self.exact.matched_count
            + self.fuzzy.auto_matched_count
            + (self.rule.auto_matched_count if self.rule else 0)
            + (self.cross_currency.matched_count if self.cross_currency else 0)
            + (self.group.group_count if self.group else 0)
        )

    @property
    def total_pending_review_count(self) -> int:
        return self.fuzzy.review_count + (self.rule.review_count if self.rule else 0)


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
        mode: str = "FULL",
        date_from: date | None = None,
        date_to: date | None = None,
        rules: RuleStageConfig | None = None,
        fx: FxService | None = None,
        audit: AuditLogger | None = None,
        now: datetime | None = None,
    ) -> None:
        self._session = session
        self._run_id = run_id
        self._repository = MatchingRepository(session)
        self._blocking_config = blocking_config
        self._amount_tolerance_minor = amount_tolerance_minor
        self._matching_config = matching_config
        self._set_based_exact = set_based_exact
        if mode not in ("FAST", "FULL"):
            raise ValueError(f"mode must be FAST or FULL, got {mode!r}")
        self._mode = mode
        self._range = {"date_from": date_from, "date_to": date_to}
        self._rules = rules
        self._fx = fx
        self._audit = audit
        self._now = now

    def run(self, bank_code: str) -> OrchestratorOutcome:
        run_started = time.monotonic()
        internal_total = self._repository.count_unmatched(bank_code, "INTERNAL", **self._range)
        external_total = self._repository.count_unmatched(bank_code, "EXTERNAL", **self._range)

        exact_duration = 0.0
        set_based_matched = 0
        if self._set_based_exact:
            started = time.monotonic()
            set_based_matched = SetBasedExactStage(self._session, self._run_id).run(
                bank_code, **self._range
            )
            exact_duration += time.monotonic() - started

        internal_before_exact = self._repository.find_unmatched(
            bank_code, "INTERNAL", **self._range
        )
        external_before_exact = self._repository.find_unmatched(
            bank_code, "EXTERNAL", **self._range
        )
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

        cross_outcome: CrossCurrencyOutcome | None = None
        group_outcome: GroupMatchOutcome | None = None
        group_started = time.monotonic()
        if self._mode == "FULL" and self._rules is not None:
            cross_outcome, group_outcome = self._run_group_stage(bank_code)
        group_duration = time.monotonic() - group_started

        # Re-fetch, not reuse: this is the entire mechanism behind I2.
        internal_before_fuzzy = self._repository.find_unmatched(
            bank_code, "INTERNAL", **self._range
        )
        external_before_fuzzy = self._repository.find_unmatched(
            bank_code, "EXTERNAL", **self._range
        )

        fuzzy_started = time.monotonic()
        if self._mode == "FAST":
            fuzzy_outcome = FuzzyMatchOutcome(0, 0, 0)
        else:
            fuzzy_outcome = FuzzyMatchingStrategy(
                self._session,
                self._run_id,
                self._blocking_config,
                self._amount_tolerance_minor,
                self._matching_config,
            ).run(internal_before_fuzzy, external_before_fuzzy)
        fuzzy_duration = time.monotonic() - fuzzy_started

        rule_outcome: RuleMatchOutcome | None = None
        rule_started = time.monotonic()
        if self._mode == "FULL" and self._rules is not None:
            rule_outcome = self._run_rule_stage(bank_code)
        rule_duration = time.monotonic() - rule_started

        remaining_externals = self._repository.count_unmatched(bank_code, "EXTERNAL", **self._range)
        pending_review = fuzzy_outcome.review_count + (
            rule_outcome.review_count if rule_outcome else 0
        )
        externals_matched = external_total - remaining_externals - pending_review

        total_matchable = externals_matched
        metrics = RunMetrics(
            exact_duration_seconds=exact_duration,
            fuzzy_duration_seconds=fuzzy_duration,
            total_duration_seconds=time.monotonic() - run_started,
            exact_match_rate_of_total=_safe_rate(exact_outcome.matched_count, external_total),
            exact_match_rate_of_matchable=_safe_rate(exact_outcome.matched_count, total_matchable),
            overall_match_rate_of_total=_safe_rate(externals_matched, external_total),
        )
        return OrchestratorOutcome(
            exact=exact_outcome,
            fuzzy=fuzzy_outcome,
            internal_pool_size_before_exact=internal_total,
            external_pool_size_before_exact=external_total,
            internal_pool_size_before_fuzzy=len(internal_before_fuzzy),
            external_pool_size_before_fuzzy=len(external_before_fuzzy),
            metrics=metrics,
            rule=rule_outcome,
            cross_currency=cross_outcome,
            group=group_outcome,
            rule_duration_seconds=rule_duration + group_duration,
            externals_matched=externals_matched,
        )

    def _run_group_stage(
        self, bank_code: str
    ) -> tuple[CrossCurrencyOutcome | None, GroupMatchOutcome]:
        assert self._rules is not None
        rules = self._rules
        cross_outcome: CrossCurrencyOutcome | None = None
        if self._fx is not None:
            cross_outcome = CrossCurrencyStrategy(
                self._session,
                self._run_id,
                self._fx,
                tolerance_percent=rules.fx_tolerance_percent,
                audit=self._audit,
                now=self._now,
            ).run(*self._pools(bank_code))
        group_outcome = GroupMatchingStrategy(
            self._session,
            self._run_id,
            rules.group,
            fx=self._fx,
            audit=self._audit,
            now=self._now,
        ).run(*self._pools(bank_code))
        return cross_outcome, group_outcome

    def _run_rule_stage(self, bank_code: str) -> RuleMatchOutcome:
        assert self._rules is not None
        return RuleBasedMatchingStrategy(
            self._session,
            self._run_id,
            self._rules.registry,
            self._blocking_config,
            self._matching_config,
        ).run(*self._pools(bank_code))

    def _pools(
        self, bank_code: str
    ) -> tuple[list[NormalisedTransaction], list[NormalisedTransaction]]:
        """The current unmatched internal and external pools, fetched fresh."""
        return (
            self._repository.find_unmatched(bank_code, "INTERNAL", **self._range),
            self._repository.find_unmatched(bank_code, "EXTERNAL", **self._range),
        )


def _safe_rate(numerator: int, denominator: int) -> float:
    """0.0 on an empty pool, not a ZeroDivisionError."""
    if denominator <= 0:
        return 0.0
    return numerator / denominator
