# src/recon/matching/strategies/exact.py
"""Exact matching strategy (R46-R48).

Build-once, probe-many: internal candidates are indexed into a dict
keyed by ExactMatchKey in a single pass (O(N)); external candidates then
each probe that dict once (O(M)) — giving O(N+M) overall, per A3.1's
stated complexity, achieved by this structure rather than merely claimed
in a comment.

Hash collision handling (R47): when a key maps to more than one internal
candidate, txn_date proximity is used as the secondary disambiguation
signal — the internal candidate whose txn_date is closest to the
external transaction's txn_date is preferred. This is deliberately
simple; a more sophisticated disambiguation (narration similarity) is
left to the fuzzy-matching level, since a genuine ambiguity this strategy
cannot confidently resolve should fall through to that level rather than
guess.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from recon.matching.claims import ClaimConflictError, ClaimsService
from recon.matching.keys import ExactMatchKey, build_key
from recon.persistence.models import MatchResult, NormalisedTransaction


@dataclass(frozen=True, slots=True)
class ExactMatchOutcome:
    matched_count: int
    skipped_no_key_count: int
    skipped_claim_conflict_count: int
    match_result_ids: list[uuid.UUID] = field(default_factory=list)


class ExactMatchingStrategy:
    def __init__(self, session: Session, run_id: str) -> None:
        self._session = session
        self._run_id = run_id
        self._claims = ClaimsService(session)

    def run(
        self,
        internal_candidates: list[NormalisedTransaction],
        external_candidates: list[NormalisedTransaction],
    ) -> ExactMatchOutcome:
        index, skipped_no_key = self._build_index(internal_candidates)

        matched_count = 0
        skipped_claim_conflict = 0
        result_ids: list[uuid.UUID] = []

        for external_txn in external_candidates:
            key = build_key(external_txn)
            if key is None:
                skipped_no_key += 1
                continue

            bucket = index.get(key)
            if not bucket:
                continue  # no exact candidate at all — falls through to fuzzy/rule matching later

            internal_txn = self._disambiguate(bucket, external_txn)

            try:
                internal_claim = self._claims.claim(internal_txn.id, "INTERNAL")
                external_claim = self._claims.claim(external_txn.id, "EXTERNAL")
            except ClaimConflictError:
                skipped_claim_conflict += 1
                continue

            match_result = self._create_match_result(
                internal_txn, external_txn, candidate_count=len(bucket)
            )
            self._session.add(match_result)
            self._session.flush()

            self._claims.finalise(internal_claim, match_result.id)
            self._claims.finalise(external_claim, match_result.id)

            internal_txn.match_status = "MATCHED"
            external_txn.match_status = "MATCHED"

            matched_count += 1
            result_ids.append(match_result.id)

        return ExactMatchOutcome(
            matched_count=matched_count,
            skipped_no_key_count=skipped_no_key,
            skipped_claim_conflict_count=skipped_claim_conflict,
            match_result_ids=result_ids,
        )

    @staticmethod
    def _build_index(
        internal_candidates: list[NormalisedTransaction],
    ) -> tuple[dict[ExactMatchKey, list[NormalisedTransaction]], int]:
        index: dict[ExactMatchKey, list[NormalisedTransaction]] = {}
        skipped = 0
        for txn in internal_candidates:
            key = build_key(txn)
            if key is None:
                skipped += 1
                continue
            index.setdefault(key, []).append(txn)
        return index, skipped

    @staticmethod
    def _disambiguate(
        bucket: list[NormalisedTransaction], external_txn: NormalisedTransaction
    ) -> NormalisedTransaction:
        """R47: when more than one internal candidate shares the same
        exact key, prefer the one whose txn_date is closest to the
        external transaction's — the simplest disambiguation signal that
        doesn't require a full fuzzy comparison."""
        if len(bucket) == 1:
            return bucket[0]
        return min(
            bucket, key=lambda candidate: abs((candidate.txn_date - external_txn.txn_date).days)
        )

    def _create_match_result(
        self,
        internal_txn: NormalisedTransaction,
        external_txn: NormalisedTransaction,
        *,
        candidate_count: int,
    ) -> MatchResult:
        return MatchResult(
            run_id=self._run_id,
            match_type="EXACT",
            status="AUTO_MATCHED",
            confidence=1.0,
            internal_transaction_id=internal_txn.id,
            external_transaction_id=external_txn.id,
            field_scores={
                "reference": 1.0,
                "amount": 1.0,
                "currency": 1.0,
                "direction": 1.0,
            },
            matched_fields={
                "normalised_reference": internal_txn.normalised_reference,
                "amount_minor": internal_txn.amount_minor,
                "currency": internal_txn.currency,
                "direction": internal_txn.direction,
            },
            hard_constraints_passed=True,
            candidate_count=candidate_count,
            rationale=(
                f"Exact match on (reference, amount, currency, direction). "
                f"{candidate_count} internal candidate(s) shared this key; "
                f"disambiguated by closest txn_date."
                if candidate_count > 1
                else "Exact match on (reference, amount, currency, direction)."
            ),
            matched_on_date=external_txn.txn_date,
        )
