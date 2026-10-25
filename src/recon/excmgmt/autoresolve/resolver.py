# src/recon/excmgmt/autoresolve/resolver.py
"""Tier 1 auto-resolution (A4.2, Day 4).

An OPEN Tier 1 exception is acted on by its category:

  DUPLICATE_INTERNAL   void the duplicate ledger row and link it to the original
  FEE_DEDUCTION        write a ledger adjustment for the fee and match the pair
  TIMEZONE_OFFSET      re-match with the date adjusted
  REFERENCE_TRUNCATED  re-match on the reference prefix
  DATE_MISMATCH / AMOUNT_MISMATCH (within the auto limits)  match the pair
  PARTIAL_MATCH / NETTED_SETTLEMENT  the group already matched on an exact sum

"Match the pair" means: confirm the review-band match the exception was raised
from, or accept an already-matched pair, or (for a leftover pair) claim both
sides and write a RULE match. Nothing is ever deleted.

If the action cannot be carried out safely (a claim conflict, a duplicate that is
already matched), the exception is NOT forced through: it moves to Tier 2 with
the reason recorded. Every outcome is an exception event and an audit entry.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.audit.chains import EXCEPTIONS_CHAIN
from recon.audit.logger import AuditLogger
from recon.domain.enums import AuditActionType, ExceptionCategory
from recon.matching.claims import ClaimConflictError, ClaimsService
from recon.matching.review import MatchReviewService, ReviewError
from recon.persistence.models import (
    ExceptionEvent,
    LedgerAdjustment,
    MatchClaim,
    MatchResult,
    NormalisedTransaction,
    ReconException,
)
from recon.persistence.repositories.matching import MatchingRepository

ACTOR = "system:auto-resolver"
_RULE_CONFIDENCE = Decimal("0.900")  # above the 0.85 auto-match line, below an exact match

_RULE_BY_CATEGORY: dict[ExceptionCategory, str] = {
    ExceptionCategory.AMOUNT_MISMATCH: "AMOUNT_TOLERANCE_AUTO",
    ExceptionCategory.DATE_MISMATCH: "DATE_OFFSET_AUTO",
    ExceptionCategory.TIMEZONE_OFFSET: "TIMEZONE_DATE_ADJUSTED",
    ExceptionCategory.REFERENCE_TRUNCATED: "REFERENCE_PREFIX_REMATCH",
    ExceptionCategory.FEE_DEDUCTION: "FEE_DEDUCTION_ADJUSTED",
}


class NotAutoResolvableError(Exception):
    """The category is Tier 1, but this instance cannot be resolved automatically."""


@dataclass(frozen=True, slots=True)
class AutoResolveOutcome:
    resolved_count: int = 0
    escalated_count: int = 0
    resolved_by_category: dict[str, int] = field(default_factory=dict)


class AutoResolver:
    def __init__(self, session: Session, audit: AuditLogger) -> None:
        self._session = session
        self._audit = audit
        self._claims = ClaimsService(session)

    def run(self, bank_code: str, now: datetime, run_id: str | None = None) -> AutoResolveOutcome:
        query = (
            select(ReconException)
            .where(
                ReconException.bank_code == bank_code,
                ReconException.status == "OPEN",
                ReconException.assigned_tier == 1,
            )
            .order_by(ReconException.created_at, ReconException.id)
            .with_for_update(skip_locked=True)
        )
        if run_id is not None:
            query = query.where(ReconException.run_id == run_id)

        resolved: Counter[str] = Counter()
        escalated = 0
        for exception in self._session.scalars(query).all():
            try:
                with self._session.begin_nested():  # a failed action leaves no half-done work
                    detail = self._act(exception, now)
            except (NotAutoResolvableError, ClaimConflictError, ReviewError) as exc:
                self._escalate(exception, str(exc) or type(exc).__name__, now)
                escalated += 1
                continue
            self._resolve(exception, detail, now)
            resolved[exception.category] += 1
        self._session.flush()
        return AutoResolveOutcome(sum(resolved.values()), escalated, dict(resolved))

    # -- actions ---------------------------------------------------------

    def _act(self, exception: ReconException, now: datetime) -> str:
        category = ExceptionCategory(exception.category)
        records = exception.affected_records
        if category is ExceptionCategory.DUPLICATE_INTERNAL:
            return self._void_duplicate(records)
        if category in (ExceptionCategory.PARTIAL_MATCH, ExceptionCategory.NETTED_SETTLEMENT):
            return "the group matched on an exact sum; nothing further to do"
        rule_id = _RULE_BY_CATEGORY.get(category)
        if rule_id is None:
            raise NotAutoResolvableError(f"{category.value} has no automatic action")
        internal = self._transaction(records["internal_transaction_id"])
        external = self._transaction(records["external_transaction_id"])
        how = self._settle_pair(
            records,
            internal,
            external,
            rule_id=rule_id,
            rationale=exception.rationale,
            now=now,
        )
        if category is ExceptionCategory.FEE_DEDUCTION:
            how += "; " + self._post_fee_adjustment(exception, internal, external, now)
        return how

    def _transaction(self, transaction_id: str) -> NormalisedTransaction:
        row = self._session.scalars(
            select(NormalisedTransaction).where(
                NormalisedTransaction.id == uuid.UUID(transaction_id)
            )
        ).first()
        if row is None:
            raise NotAutoResolvableError(f"transaction {transaction_id} no longer exists")
        return row

    def _void_duplicate(self, records: dict[str, Any]) -> str:
        copy = self._transaction(records["normalised_transaction_ids"][0])
        original_id = uuid.UUID(records["original_transaction_id"])
        held = self._session.scalar(
            select(MatchClaim.id).where(
                MatchClaim.normalised_transaction_id == copy.id, MatchClaim.status == "ACTIVE"
            )
        )
        if copy.match_status != "UNMATCHED" or held is not None:
            raise NotAutoResolvableError(
                "the duplicate is already matched; unmatching it needs an analyst"
            )
        copy.match_status = "VOIDED"
        copy.voided_original_id = original_id
        return f"voided the duplicate ledger row and linked it to original {original_id}"

    def _settle_pair(
        self,
        records: dict[str, Any],
        internal: NormalisedTransaction,
        external: NormalisedTransaction,
        *,
        rule_id: str,
        rationale: str,
        now: datetime,
    ) -> str:
        result_id = records.get("match_result_id")
        if result_id is not None:
            result = self._session.get(MatchResult, uuid.UUID(result_id))
            if result is None:
                raise NotAutoResolvableError("the match result no longer exists")
            if result.status == "PENDING_REVIEW":
                MatchReviewService(self._session, self._audit).confirm(
                    result.id,
                    reviewer=ACTOR,
                    now=now,
                    note=f"{rule_id}: {rationale}",
                    automated=True,
                )
                return f"{rule_id}: confirmed the pending match"
            if result.status in ("AUTO_MATCHED", "CONFIRMED"):
                return f"{rule_id}: the pair was already matched; the difference is recorded"
            raise NotAutoResolvableError(f"the pair's match is {result.status}")
        if internal.match_status != "UNMATCHED" or external.match_status != "UNMATCHED":
            raise NotAutoResolvableError("one of the transactions is no longer unmatched")
        if (internal.id, external.id) in MatchingRepository(self._session).rejected_pairs(
            [internal.id]
        ):
            raise NotAutoResolvableError("a person unmatched this pair; it is not re-matched")
        internal_claim, external_claim = self._claims.claim_pair(internal.id, external.id)
        match = MatchResult(
            run_id=f"auto-resolve:{rule_id}",
            match_type="RULE",
            status="AUTO_MATCHED",
            confidence=_RULE_CONFIDENCE,
            internal_transaction_id=internal.id,
            external_transaction_id=external.id,
            field_scores={"auto_resolution": 1.0},
            matched_fields={"normalised_reference": external.normalised_reference},
            hard_constraints_passed=True,
            rule_id=rule_id,
            candidate_count=1,
            rationale=f"{rule_id}: {rationale}",
            matched_on_date=external.txn_date,
            weights_version=None,
        )
        self._session.add(match)
        self._session.flush()
        self._claims.finalise(internal_claim, match.id)
        self._claims.finalise(external_claim, match.id)
        internal.match_status = "MATCHED"
        external.match_status = "MATCHED"
        return f"{rule_id}: matched the pair"

    def _post_fee_adjustment(
        self,
        exception: ReconException,
        internal: NormalisedTransaction,
        external: NormalisedTransaction,
        now: datetime,
    ) -> str:
        fee = abs(internal.amount_minor - external.amount_minor)
        self._session.add(
            LedgerAdjustment(
                exception_id=exception.id,
                bank_code=exception.bank_code,
                adjustment_type="BANK_FEE",
                internal_transaction_id=internal.id,
                external_transaction_id=external.id,
                amount_minor=fee,
                currency=internal.currency,
                rationale=f"Bank fee of {fee} minor units matches the published fee schedule.",
                created_by=ACTOR,
                created_at=now,
            )
        )
        self._session.flush()
        return f"posted a {fee} minor-unit bank-fee adjustment"

    # -- outcomes --------------------------------------------------------

    def _resolve(self, exception: ReconException, detail: str, now: datetime) -> None:
        before = {"status": exception.status, "tier": exception.assigned_tier}
        exception.status = "RESOLVED"
        exception.resolved_at = now
        exception.resolved_by = ACTOR
        exception.resolution_details = detail
        self._session.add(
            ExceptionEvent(
                exception_id=exception.id,
                event_type="RESOLVED",
                from_tier=1,
                to_tier=1,
                actor=ACTOR,
                detail=f"auto-resolved: {detail}",
                occurred_at=now,
            )
        )
        self._audit.append(
            chain_id=EXCEPTIONS_CHAIN,
            actor_type="SYSTEM",
            actor_id="auto-resolver",
            action_type=AuditActionType.RESOLVE,
            occurred_at=now,
            affected_records={"exception_id": str(exception.id), **exception.affected_records},
            before_state=before,
            after_state={"status": "RESOLVED", "tier": exception.assigned_tier},
            rationale=f"{exception.category}: {detail}",
        )

    def _escalate(self, exception: ReconException, reason: str, now: datetime) -> None:
        before = {"status": exception.status, "tier": exception.assigned_tier}
        exception.assigned_tier = 2
        self._session.add(
            ExceptionEvent(
                exception_id=exception.id,
                event_type="ESCALATED",
                from_tier=1,
                to_tier=2,
                actor=ACTOR,
                detail=f"auto-resolution not possible: {reason}",
                occurred_at=now,
            )
        )
        self._audit.append(
            chain_id=EXCEPTIONS_CHAIN,
            actor_type="SYSTEM",
            actor_id="auto-resolver",
            action_type=AuditActionType.ESCALATE,
            occurred_at=now,
            affected_records={"exception_id": str(exception.id)},
            before_state=before,
            after_state={"status": exception.status, "tier": 2},
            rationale=f"auto-resolution not possible: {reason}",
        )
