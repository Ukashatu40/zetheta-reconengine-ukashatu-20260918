# src/recon/matching/manual.py
"""Manual matches and unmatching (A9.2: ANALYST may add manual matches; A4.3 MATCH
and UNMATCH actions).

create   pairs one INTERNAL and one EXTERNAL transaction of the same bank that are
         both free (UNMATCHED, no active claim). A pair parked for review must be
         confirmed or rejected first, never silently overridden.
unmatch  undoes a match: every row of the match (the whole group for a split or
         netted match) is marked REJECTED, its claims released, and its transactions
         returned to the pool. A REJECTED pair is never matched again by any level
         (see MatchingRepository.rejected_pairs).

Neither commits; both write to the matching audit chain with the person and their
reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.audit.chains import MATCHING_CHAIN
from recon.audit.logger import AuditLogger
from recon.domain.enums import AuditActionType
from recon.matching.claims import ClaimConflictError, ClaimsService
from recon.persistence.models import MatchClaim, MatchResult, NormalisedTransaction

_UNMATCHABLE = ("AUTO_MATCHED", "CONFIRMED")


class ManualMatchError(ValueError):
    """Base class; `status_code` is the HTTP status the API should answer with."""

    status_code = 422


class TransactionNotFoundError(ManualMatchError):
    status_code = 404


class InvalidPairError(ManualMatchError):
    status_code = 422


class TransactionUnavailableError(ManualMatchError):
    status_code = 409


class MatchNotUnmatchableError(ManualMatchError):
    status_code = 409


class ManualMatchService:
    def __init__(self, session: Session, audit: AuditLogger) -> None:
        self._session = session
        self._audit = audit
        self._claims = ClaimsService(session)

    def create(
        self,
        internal_id: uuid.UUID,
        external_id: uuid.UUID,
        *,
        user: str,
        note: str,
        now: datetime,
    ) -> MatchResult:
        internal = self._load(internal_id, "internal")
        external = self._load(external_id, "external")
        if internal.source != "INTERNAL" or external.source != "EXTERNAL":
            raise InvalidPairError("a match pairs one internal transaction with one external")
        if internal.bank_code != external.bank_code:
            raise InvalidPairError("both transactions must belong to the same bank")
        for txn in (internal, external):
            if txn.match_status != "UNMATCHED" or self._claims.is_claimed(txn.id):
                raise TransactionUnavailableError(
                    f"transaction {txn.id} is already matched or held for review"
                )
        try:
            internal_claim, external_claim = self._claims.claim_pair(internal.id, external.id)
        except ClaimConflictError as exc:
            raise TransactionUnavailableError("a transaction was claimed concurrently") from exc

        result = MatchResult(
            run_id=f"manual:{user}",
            match_type="MANUAL",
            status="CONFIRMED",
            confidence=Decimal("1.000"),
            internal_transaction_id=internal.id,
            external_transaction_id=external.id,
            field_scores={},
            matched_fields={
                "amount_minor_difference": abs(internal.amount_minor - external.amount_minor)
            },
            hard_constraints_passed=True,
            rule_id="MANUAL",
            candidate_count=1,
            rationale=note,
            matched_on_date=external.txn_date,
            reviewed_by=user,
            reviewed_at=now,
            weights_version=None,
        )
        self._session.add(result)
        self._session.flush()
        self._claims.finalise(internal_claim, result.id)
        self._claims.finalise(external_claim, result.id)
        internal.match_status = "MATCHED"
        external.match_status = "MATCHED"
        self._audit.append(
            chain_id=MATCHING_CHAIN,
            actor_type="USER",
            actor_id=f"api-key:{user}",
            action_type=AuditActionType.MATCH,
            occurred_at=now,
            affected_records={
                "match_result_id": str(result.id),
                "internal_transaction_id": str(internal.id),
                "external_transaction_id": str(external.id),
            },
            after_state={"match_type": "MANUAL", "status": "CONFIRMED"},
            rationale=note,
        )
        self._session.flush()
        return result

    def unmatch(
        self, match_id: uuid.UUID, *, user: str, reason: str, now: datetime
    ) -> list[MatchResult]:
        first = self._session.get(MatchResult, match_id, with_for_update=True)
        if first is None:
            raise TransactionNotFoundError("match not found")
        results = [first]
        if first.match_group_id is not None:
            results = list(
                self._session.scalars(
                    select(MatchResult)
                    .where(MatchResult.match_group_id == first.match_group_id)
                    .order_by(MatchResult.created_at, MatchResult.id)
                    .with_for_update()
                )
            )
        if any(r.status not in _UNMATCHABLE for r in results):
            raise MatchNotUnmatchableError(
                "only an automatic or confirmed match can be unmatched "
                f"(this one is {first.status})"
            )

        result_ids = [r.id for r in results]
        for claim in self._session.scalars(
            select(MatchClaim).where(
                MatchClaim.match_result_id.in_(result_ids), MatchClaim.status == "ACTIVE"
            )
        ):
            self._claims.release(claim)
        txn_ids = {r.internal_transaction_id for r in results} | {
            r.external_transaction_id for r in results
        }
        for txn in self._session.scalars(
            select(NormalisedTransaction)
            .where(NormalisedTransaction.id.in_(txn_ids))
            .with_for_update()
        ):
            txn.match_status = "UNMATCHED"
        before = {"status": first.status, "match_type": first.match_type}
        for result in results:
            result.status = "REJECTED"
            result.reviewed_by = user
            result.reviewed_at = now
        self._audit.append(
            chain_id=MATCHING_CHAIN,
            actor_type="USER",
            actor_id=f"api-key:{user}",
            action_type=AuditActionType.UNMATCH,
            occurred_at=now,
            affected_records={
                "match_result_ids": [str(i) for i in result_ids],
                "transaction_ids": sorted(str(t) for t in txn_ids),
            },
            before_state=before,
            after_state={"status": "REJECTED", "transactions": "UNMATCHED"},
            rationale=reason,
        )
        self._session.flush()
        return results

    def _load(self, transaction_id: uuid.UUID, label: str) -> NormalisedTransaction:
        row = self._session.scalars(
            select(NormalisedTransaction)
            .where(NormalisedTransaction.id == transaction_id)
            .with_for_update()
        ).first()
        if row is None:
            raise TransactionNotFoundError(f"the {label} transaction was not found")
        return row
