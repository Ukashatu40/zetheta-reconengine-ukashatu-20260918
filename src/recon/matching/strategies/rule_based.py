# src/recon/matching/strategies/rule_based.py
"""Rule-based matching strategy: evaluates a RuleRegistry against the
blocked candidates of each external transaction.

Safety behaviour:
  - More than one candidate satisfying a rule, or a candidate list
    truncated by the blocking cap, is AMBIGUOUS: nothing is claimed, and
    the pair is left for a later level or a human.
  - Internals consumed earlier in this run are excluded from later
    candidate lists, so a consumed internal is never re-offered.
  - Status is decided on the confidence quantised to 3 decimals (IB-04),
    against the configured auto-match threshold. A rule result at or
    below it is written PENDING_REVIEW: claimed, not marked matched.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy.orm import Session

from recon.config.matching_models import MatchingConfig
from recon.matching.blocking.candidates import BlockingConfig, CandidateGenerator
from recon.matching.claims import ClaimConflictError, ClaimsService
from recon.matching.rules.base import Rule, RuleRegistry, RuleResult
from recon.persistence.models import MatchResult, NormalisedTransaction


@dataclass(frozen=True, slots=True)
class RuleMatchOutcome:
    auto_matched_count: int
    review_count: int
    ambiguous_count: int
    skipped_claim_conflict_count: int
    match_result_ids: list[uuid.UUID] = field(default_factory=list)


class RuleBasedMatchingStrategy:
    def __init__(
        self,
        session: Session,
        run_id: str,
        registry: RuleRegistry,
        blocking_config: BlockingConfig,
        matching_config: MatchingConfig,
    ) -> None:
        self._session = session
        self._run_id = run_id
        self._registry = registry
        self._blocking_config = blocking_config
        self._matching_config = matching_config
        self._claims = ClaimsService(session)

    def run(
        self,
        internal_candidates: list[NormalisedTransaction],
        external_candidates: list[NormalisedTransaction],
    ) -> RuleMatchOutcome:
        generator = CandidateGenerator(internal_candidates, self._blocking_config)
        consumed: set[uuid.UUID] = set()
        auto_matched = review = ambiguous = conflicts = 0
        result_ids: list[uuid.UUID] = []

        for external_txn in external_candidates:
            candidate_set = generator.generate(external_txn)
            available = [c for c in candidate_set.candidates if c.id not in consumed]
            hits = self._evaluate(available, external_txn)
            if not hits:
                continue
            if len(hits) > 1 or candidate_set.truncated:
                ambiguous += 1
                continue

            internal_txn, rule, rule_result = hits[0]
            try:
                internal_claim, external_claim = self._claims.claim_pair(
                    internal_txn.id, external_txn.id
                )
            except ClaimConflictError:
                conflicts += 1
                continue

            confidence = Decimal(str(rule_result.confidence_contribution)).quantize(
                Decimal("0.001"), rounding=ROUND_HALF_EVEN
            )
            is_auto = confidence > Decimal(
                str(self._matching_config.thresholds.auto_match_confidence)
            )

            match_result = self._create_match_result(
                internal_txn,
                external_txn,
                rule,
                rule_result=rule_result,
                confidence=confidence,
                is_auto=is_auto,
                candidate_count=len(available),
            )
            self._session.add(match_result)
            self._session.flush()
            self._claims.finalise(internal_claim, match_result.id)
            self._claims.finalise(external_claim, match_result.id)
            consumed.add(internal_txn.id)

            if is_auto:
                internal_txn.match_status = "MATCHED"
                external_txn.match_status = "MATCHED"
                auto_matched += 1
            else:
                review += 1
            result_ids.append(match_result.id)

        return RuleMatchOutcome(auto_matched, review, ambiguous, conflicts, result_ids)

    def _evaluate(
        self, candidates: list[NormalisedTransaction], external_txn: NormalisedTransaction
    ) -> list[tuple[NormalisedTransaction, Rule, RuleResult]]:
        hits: list[tuple[NormalisedTransaction, Rule, RuleResult]] = []
        for internal_txn in candidates:
            outcome = self._registry.evaluate(internal_txn, external_txn)
            if outcome is not None:
                rule, rule_result = outcome
                hits.append((internal_txn, rule, rule_result))
        return hits

    def _create_match_result(
        self,
        internal_txn: NormalisedTransaction,
        external_txn: NormalisedTransaction,
        rule: Rule,
        *,
        rule_result: RuleResult,
        confidence: Decimal,
        is_auto: bool,
        candidate_count: int,
    ) -> MatchResult:
        return MatchResult(
            run_id=self._run_id,
            match_type="RULE",
            status="AUTO_MATCHED" if is_auto else "PENDING_REVIEW",
            confidence=confidence,
            internal_transaction_id=internal_txn.id,
            external_transaction_id=external_txn.id,
            field_scores={"rule_confidence": float(confidence)},
            matched_fields={
                "normalised_reference": internal_txn.normalised_reference,
                "internal_amount_minor": internal_txn.amount_minor,
                "external_amount_minor": external_txn.amount_minor,
            },
            hard_constraints_passed=True,
            rule_id=rule.name,
            candidate_count=candidate_count,
            rationale=rule_result.explanation,
            matched_on_date=external_txn.txn_date,
            weights_version=None,
        )
