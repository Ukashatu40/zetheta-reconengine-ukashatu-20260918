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
from typing import Any

from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recon.config.matching_models import MatchingConfig
from recon.matching.blocking.candidates import BlockingConfig, CandidateGenerator
from recon.matching.claims import ClaimConflictError, ClaimsService
from recon.matching.scoring.weighted import MatchDecision, ScoredCandidate, score_candidate
from recon.persistence.models import MatchClaim, MatchResult, NormalisedTransaction
from recon.persistence.repositories.matching import MatchingRepository

_WRITE_CHUNK = 500


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
        matching_config: MatchingConfig,
    ) -> None:
        self._session = session
        self._run_id = run_id
        self._blocking_config = blocking_config
        self._amount_tolerance_minor = amount_tolerance_minor
        self._matching_config = matching_config
        self._claims = ClaimsService(session)

    def run(
        self,
        internal_candidates: list[NormalisedTransaction],
        external_candidates: list[NormalisedTransaction],
    ) -> FuzzyMatchOutcome:
        generator = CandidateGenerator(internal_candidates, self._blocking_config)
        consumed: set[uuid.UUID] = set()
        rejected = MatchingRepository(self._session).rejected_pairs(
            t.id for t in internal_candidates
        )

        # Phase 1, in memory: choose the best candidate for each external. No database round
        # trip per record; the earlier version wrote each match as it went (about ten round
        # trips each) and spent 85% of the stage waiting for the database.
        chosen: list[tuple[ScoredCandidate, NormalisedTransaction, int]] = []
        for external_txn in external_candidates:
            candidate_set = generator.generate(external_txn)
            available = [
                c
                for c in candidate_set.candidates
                if c.id not in consumed and (c.id, external_txn.id) not in rejected
            ]
            best = self._best_candidate(external_txn, available)
            if best is None or best.decision == MatchDecision.NO_MATCH:
                continue
            consumed.add(best.internal_txn.id)
            chosen.append((best, external_txn, len(available)))

        # Phase 2: write in chunks. A chunk is one savepoint; if any claim in it conflicts (a
        # concurrent writer, or a claim made earlier in the run) it is redone pair by pair so
        # that only the conflicting pairs are skipped, exactly as before.
        auto_matched = reviewed = skipped_conflicts = 0
        result_ids: list[uuid.UUID] = []
        for start in range(0, len(chosen), _WRITE_CHUNK):
            chunk = chosen[start : start + _WRITE_CHUNK]
            batch = self._write_chunk(chunk)
            written: list[uuid.UUID | None] = (
                [self._write_one(item) for item in chunk] if batch is None else list(batch)
            )
            for item, result_id in zip(chunk, written, strict=True):
                if result_id is None:
                    skipped_conflicts += 1
                    continue
                best, external_txn, _ = item
                result_ids.append(result_id)
                if best.decision == MatchDecision.AUTO_MATCH:
                    best.internal_txn.match_status = "MATCHED"
                    external_txn.match_status = "MATCHED"
                    auto_matched += 1
                else:
                    # Review-band pairs are claimed, so no other candidate can take either side
                    # while a human decides, but are not MATCHED until a reviewer confirms.
                    reviewed += 1
            self._session.flush()

        return FuzzyMatchOutcome(
            auto_matched_count=auto_matched,
            review_count=reviewed,
            skipped_claim_conflict_count=skipped_conflicts,
            match_result_ids=result_ids,
        )

    def _write_chunk(
        self, chunk: list[tuple[ScoredCandidate, NormalisedTransaction, int]]
    ) -> list[uuid.UUID] | None:
        """Inserts every match and both of its claims as two multi-row statements (ids are made
        here, so nothing needs to be read back). None means a claim conflicted and nothing was
        written."""
        results = [{"id": uuid.uuid4(), **self._match_columns(best, n)} for best, _, n in chunk]
        claims = [
            {
                "normalised_transaction_id": txn_id,
                "match_result_id": row["id"],
                "role": role,
                "status": "ACTIVE",
            }
            for row in results
            for txn_id, role in (
                (row["internal_transaction_id"], "INTERNAL"),
                (row["external_transaction_id"], "EXTERNAL"),
            )
        ]
        try:
            with self._session.begin_nested():
                self._session.execute(insert(MatchResult), results)
                self._session.execute(insert(MatchClaim), claims)
        except IntegrityError:
            return None
        return [row["id"] for row in results]

    def _write_one(
        self, item: tuple[ScoredCandidate, NormalisedTransaction, int]
    ) -> uuid.UUID | None:
        best, external_txn, candidate_count = item
        try:
            internal_claim, external_claim = self._claims.claim_pair(
                best.internal_txn.id, external_txn.id
            )
        except ClaimConflictError:
            return None
        match_result = MatchResult(**self._match_columns(best, candidate_count))
        self._session.add(match_result)
        self._session.flush()
        self._claims.finalise(internal_claim, match_result.id)
        self._claims.finalise(external_claim, match_result.id)
        return match_result.id

    def _best_candidate(
        self, external_txn: NormalisedTransaction, candidates: list[NormalisedTransaction]
    ) -> ScoredCandidate | None:
        scored = [
            score_candidate(
                internal_txn,
                external_txn,
                amount_tolerance_minor=self._amount_tolerance_minor,
                config=self._matching_config,
            )
            for internal_txn in candidates
        ]
        matchable = [s for s in scored if s.decision != MatchDecision.NO_MATCH]
        if not matchable:
            return None
        return max(matchable, key=lambda s: s.confidence)

    def _match_columns(self, scored: ScoredCandidate, candidate_count: int) -> dict[str, Any]:
        status = "AUTO_MATCHED" if scored.decision == MatchDecision.AUTO_MATCH else "PENDING_REVIEW"
        return {
            "run_id": self._run_id,
            "match_type": "FUZZY",
            "status": status,
            "confidence": scored.confidence,
            "internal_transaction_id": scored.internal_txn.id,
            "external_transaction_id": scored.external_txn.id,
            "field_scores": scored.field_scores,
            "weights_version": scored.weights_version,
            "matched_fields": {
                "normalised_reference": scored.internal_txn.normalised_reference,
                "amount_minor": scored.internal_txn.amount_minor,
            },
            "hard_constraints_passed": scored.hard_constraints_passed,
            "candidate_count": candidate_count,
            "rationale": scored.rationale,
            "matched_on_date": scored.external_txn.txn_date,
        }
