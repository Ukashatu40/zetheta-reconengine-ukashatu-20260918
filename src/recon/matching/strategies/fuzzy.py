# src/recon/matching/strategies/fuzzy.py
"""Fuzzy matching strategy (R49-R51), consuming Increment 3's
CandidateGenerator output and Increment 4's weighted scoring.

AUTO_MATCH candidates are claimed and written exactly as
ExactMatchingStrategy does. REVIEW candidates are ALSO written to
match_results (satisfying Day 3's literal "write matches above 0.60
threshold" instruction) but with status='PENDING_REVIEW' — per AE-10,
already implemented at the schema level in WP4 Increment 2's
match_results.status check constraint — and are claimed too, so a
review-band candidate isn't left available for a DIFFERENT candidate to
also claim while a human decides on the first one. NO_MATCH candidates
are not written at all.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from recon.matching.blocking.candidates import BlockingConfig, CandidateGenerator
from recon.matching.claims import ClaimConflictError, ClaimsService
from recon.matching.scoring.weighted import MatchDecision, ScoredCandidate, score_candidate
from recon.persistence.models import MatchResult, NormalisedTransaction


@dataclass(frozen=True, slots=True)
class FuzzyMatchOutcome:
    auto_matched_count: int
    review_count: int
    skipped_claim_conflict_count: int
    match_result_ids: list[uuid.UUID] = field(default_factory=list)


class FuzzyMatchingStrategy:
    def __init__(
        self,
        session: Session,
        run_id: str,
        blocking_config: BlockingConfig,
        amount_tolerance_minor: int,
    ) -> None:
        self._session = session
        self._run_id = run_id
        self._blocking_config = blocking_config
        self._amount_tolerance_minor = amount_tolerance_minor
        self._claims = ClaimsService(session)

    def run(
        self,
        internal_candidates: list[NormalisedTransaction],
        external_candidates: list[NormalisedTransaction],
    ) -> FuzzyMatchOutcome:
        generator = CandidateGenerator(internal_candidates, self._blocking_config)

        auto_matched = 0
        reviewed = 0
        skipped_conflicts = 0
        result_ids: list[uuid.UUID] = []

        for external_txn in external_candidates:
            candidate_set = generator.generate(external_txn)
            best = self._best_candidate(external_txn, candidate_set.candidates)

            if best is None or best.decision == MatchDecision.NO_MATCH:
                continue

            try:
                internal_claim, external_claim = self._claims.claim_pair(
                    best.internal_txn.id, external_txn.id
                )
            except ClaimConflictError:
                skipped_conflicts += 1
                continue

            match_result = self._create_match_result(
                best, candidate_count=len(candidate_set.candidates)
            )
            self._session.add(match_result)
            self._session.flush()

            self._claims.finalise(internal_claim, match_result.id)
            self._claims.finalise(external_claim, match_result.id)

            if best.decision == MatchDecision.AUTO_MATCH:
                best.internal_txn.match_status = "MATCHED"
                external_txn.match_status = "MATCHED"
                auto_matched += 1
            else:
                # Review-band candidates are claimed (preventing a
                # different candidate from also claiming either side
                # while a human decides) but NOT marked MATCHED — that
                # transition happens only on human confirmation, which
                # this codebase does not yet implement (no reviewer
                # workflow exists before WP5).
                reviewed += 1

            result_ids.append(match_result.id)

        return FuzzyMatchOutcome(
            auto_matched_count=auto_matched,
            review_count=reviewed,
            skipped_claim_conflict_count=skipped_conflicts,
            match_result_ids=result_ids,
        )

    def _best_candidate(
        self, external_txn: NormalisedTransaction, candidates: list[NormalisedTransaction]
    ) -> ScoredCandidate | None:
        scored = [
            score_candidate(
                internal_txn, external_txn, amount_tolerance_minor=self._amount_tolerance_minor
            )
            for internal_txn in candidates
        ]
        matchable = [s for s in scored if s.decision != MatchDecision.NO_MATCH]
        if not matchable:
            return None
        return max(matchable, key=lambda s: s.confidence)

    def _create_match_result(self, scored: ScoredCandidate, *, candidate_count: int) -> MatchResult:
        status = "AUTO_MATCHED" if scored.decision == MatchDecision.AUTO_MATCH else "PENDING_REVIEW"
        return MatchResult(
            run_id=self._run_id,
            match_type="FUZZY",
            status=status,
            confidence=scored.confidence,
            internal_transaction_id=scored.internal_txn.id,
            external_transaction_id=scored.external_txn.id,
            field_scores=scored.field_scores,
            matched_fields={
                "normalised_reference": scored.internal_txn.normalised_reference,
                "amount_minor": scored.internal_txn.amount_minor,
            },
            hard_constraints_passed=scored.hard_constraints_passed,
            candidate_count=candidate_count,
            rationale=scored.rationale,
            matched_on_date=scored.external_txn.txn_date,
        )
