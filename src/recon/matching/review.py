# src/recon/matching/review.py
"""Human review of PENDING_REVIEW matches (A3.4's 0.60-0.85 band, AE-10).

confirm: result -> CONFIRMED, both transactions -> MATCHED, claims kept.
reject:  result -> REJECTED, both claims released, transactions return to
         the unmatched pool. A rejected pair is not proposed again (the
         fuzzy strategy skips it).

A decision is final: only a PENDING_REVIEW result can be reviewed, under a
row lock, so two reviewers cannot both decide. Confirm also checks that the
pair's claims and transactions are still in the state matching left them,
and changes nothing if they are not.

Does not commit. The caller commits.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.audit.chains import MATCHING_CHAIN
from recon.audit.logger import AuditLogger
from recon.domain.enums import AuditActionType
from recon.matching.claims import ClaimsService
from recon.persistence.models import MatchClaim, MatchResult, NormalisedTransaction

EXPECTED_MATCH_PAIR_COUNT = 2


class ReviewError(Exception):
    """Base class for review failures."""


class MatchNotFoundError(ReviewError):
    """No match result with that id."""


class MatchNotPendingError(ReviewError):
    """The match result has already been decided (or never needed review)."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"match is already {status}")


class ReviewStateError(ReviewError):
    """The pair's claims or transactions are not in the expected state."""


class MatchReviewService:
    def __init__(self, session: Session, audit: AuditLogger) -> None:
        self._session = session
        self._audit = audit

    def confirm(
        self, match_id: uuid.UUID, *, reviewer: str, now: datetime, note: str | None = None
    ) -> MatchResult:
        result = self._lock_pending(match_id)
        transactions = self._transactions(result)
        claims = self._active_claims(result.id)
        expected = {result.internal_transaction_id, result.external_transaction_id}
        if (
            len(claims) != EXPECTED_MATCH_PAIR_COUNT
            or {c.normalised_transaction_id for c in claims} != expected
        ):
            raise ReviewStateError("the pair's claims are missing or inconsistent")
        if any(t.match_status != "UNMATCHED" for t in transactions):
            raise ReviewStateError("one of the transactions is no longer unmatched")

        for transaction in transactions:
            transaction.match_status = "MATCHED"
        self._record(result, "CONFIRMED", reviewer, now)
        self._audit_review(
            result,
            AuditActionType.MATCH_CONFIRM,
            reviewer=reviewer,
            now=now,
            rationale=note or "confirmed without a note",
            after_status="CONFIRMED",
        )
        self._session.flush()
        return result

    def reject(
        self, match_id: uuid.UUID, *, reviewer: str, now: datetime, reason: str
    ) -> MatchResult:
        result = self._lock_pending(match_id)
        claims_service = ClaimsService(self._session)
        for claim in self._active_claims(result.id):
            claims_service.release(claim)
        self._record(result, "REJECTED", reviewer, now)
        self._audit_review(
            result,
            AuditActionType.MATCH_REJECT,
            reviewer=reviewer,
            now=now,
            rationale=reason,
            after_status="REJECTED",
        )
        self._session.flush()
        return result

    # -- internals ------------------------------------------------------

    def _lock_pending(self, match_id: uuid.UUID) -> MatchResult:
        result = self._session.get(MatchResult, match_id, with_for_update=True)
        if result is None:
            raise MatchNotFoundError(str(match_id))
        if result.status != "PENDING_REVIEW":
            raise MatchNotPendingError(result.status)
        return result

    def _transactions(self, result: MatchResult) -> list[NormalisedTransaction]:
        rows = list(
            self._session.scalars(
                select(NormalisedTransaction)
                .where(
                    NormalisedTransaction.id.in_(
                        [result.internal_transaction_id, result.external_transaction_id]
                    )
                )
                .with_for_update()
            )
        )
        if len(rows) != 2:  # noqa: PLR2004
            raise ReviewStateError("one of the pair's transactions no longer exists")
        return rows

    def _active_claims(self, match_id: uuid.UUID) -> list[MatchClaim]:
        return list(
            self._session.scalars(
                select(MatchClaim).where(
                    MatchClaim.match_result_id == match_id, MatchClaim.status == "ACTIVE"
                )
            )
        )

    @staticmethod
    def _record(result: MatchResult, status: str, reviewer: str, now: datetime) -> None:
        result.status = status
        result.reviewed_by = reviewer
        result.reviewed_at = now

    def _audit_review(
        self,
        result: MatchResult,
        action: AuditActionType,
        *,  # 🌟 Forces all remaining parameters to be explicitly named keyword arguments
        reviewer: str,
        now: datetime,
        rationale: str,
        after_status: str,
    ) -> None:
        self._audit.append(
            chain_id=MATCHING_CHAIN,
            actor_type="USER",
            actor_id=f"api-key:{reviewer}",
            action_type=action,
            occurred_at=now,
            affected_records={
                "match_result_id": str(result.id),
                "internal_transaction_id": str(result.internal_transaction_id),
                "external_transaction_id": str(result.external_transaction_id),
            },
            before_state={"status": "PENDING_REVIEW"},
            after_state={"status": after_status},
            rationale=rationale,
        )
