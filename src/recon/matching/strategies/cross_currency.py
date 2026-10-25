# src/recon/matching/strategies/cross_currency.py
"""One-to-one cross-currency matching (A3.3, A5.1).

An external entry and an internal entry with the same reference and direction
but different currencies are compared in INR: each leg is converted at the rate
for its own booking date and the pair matches when |a - b| / mean(a, b) is within
the bank's FX tolerance (1.5% commercial, 0.5% interbank). A pair outside the
tolerance is NOT matched; the classifier reports it as FX_VARIANCE with the
measured variance. When more than one internal fits, nothing is claimed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from recon.audit.chains import MATCHING_CHAIN
from recon.audit.logger import AuditLogger
from recon.domain.enums import AuditActionType
from recon.fx.convert import relative_difference_percent, to_inr_minor
from recon.fx.service import FxService
from recon.matching.claims import ClaimConflictError, ClaimsService
from recon.persistence.models import MatchResult, NormalisedTransaction
from recon.persistence.repositories.matching import MatchingRepository

_CONFIDENCE = Decimal("0.900")
_RULE_ID = "CROSS_CURRENCY_TOLERANCE"


@dataclass(slots=True)
class CrossCurrencyOutcome:
    matched_count: int = 0
    out_of_tolerance_count: int = 0
    ambiguous_count: int = 0
    missing_fx_count: int = 0
    skipped_claim_conflict_count: int = 0
    match_result_ids: list[uuid.UUID] = field(default_factory=list)


class CrossCurrencyStrategy:
    def __init__(
        self,
        session: Session,
        run_id: str,
        fx: FxService,
        *,
        tolerance_percent: Decimal,
        max_days_apart: int = 3,
        audit: AuditLogger | None = None,
        now: datetime | None = None,
    ) -> None:
        if audit is not None and now is None:
            raise ValueError("now is required when an audit logger is given")
        self._session = session
        self._run_id = run_id
        self._fx = fx
        self._tolerance = tolerance_percent
        self._max_days = max_days_apart
        self._audit = audit
        self._now = now
        self._claims = ClaimsService(session)

    def run(
        self,
        internal_candidates: list[NormalisedTransaction],
        external_candidates: list[NormalisedTransaction],
    ) -> CrossCurrencyOutcome:
        by_reference: dict[str, list[NormalisedTransaction]] = {}
        for internal in internal_candidates:
            if internal.normalised_reference:
                by_reference.setdefault(internal.normalised_reference, []).append(internal)

        outcome = CrossCurrencyOutcome()
        rejected = MatchingRepository(self._session).rejected_pairs(
            t.id for t in internal_candidates
        )
        consumed: set[uuid.UUID] = set()
        for external in external_candidates:
            peers = [
                i
                for i in by_reference.get(external.normalised_reference or "", [])
                if i.id not in consumed
                and (i.id, external.id) not in rejected
                and i.currency != external.currency
                and i.direction == external.direction
                and abs((i.txn_date - external.txn_date).days) <= self._max_days
            ]
            if not peers:
                continue
            if len(peers) > 1:
                outcome.ambiguous_count += 1
                continue
            internal = peers[0]
            comparison = self._compare(internal, external)
            if comparison is None:
                outcome.missing_fx_count += 1
                continue
            variance, details = comparison
            if variance > self._tolerance:
                outcome.out_of_tolerance_count += 1
                continue
            if self._match(internal, external, details, outcome):
                consumed.add(internal.id)
        return outcome

    def _compare(
        self, internal: NormalisedTransaction, external: NormalisedTransaction
    ) -> tuple[Decimal, dict[str, object]] | None:
        legs = []
        for leg in (internal, external):
            quote = self._fx.quote(leg.currency, leg.txn_date)
            if quote is None:
                return None
            legs.append((leg, quote, to_inr_minor(leg.amount_minor, leg.currency, quote.rate_inr)))
        (_, _, internal_inr), (_, _, external_inr) = legs
        variance = relative_difference_percent(internal_inr, external_inr)
        details: dict[str, object] = {
            "internal_currency": internal.currency,
            "external_currency": external.currency,
            "internal_inr_minor": internal_inr,
            "external_inr_minor": external_inr,
            "variance_percent": str(variance.quantize(Decimal("0.0001"))),
            "tolerance_percent": str(self._tolerance),
            "rates": {
                f"{leg.currency}@{leg.txn_date.isoformat()}": {
                    "rate_inr": str(quote.rate_inr),
                    "source": quote.source,
                    "rate_date": quote.rate_date.isoformat(),
                    "days_stale": quote.days_stale,
                }
                for leg, quote, _ in legs
            },
        }
        return variance, details

    def _match(
        self,
        internal: NormalisedTransaction,
        external: NormalisedTransaction,
        details: dict[str, object],
        outcome: CrossCurrencyOutcome,
    ) -> bool:
        try:
            internal_claim, external_claim = self._claims.claim_pair(internal.id, external.id)
        except ClaimConflictError:
            outcome.skipped_claim_conflict_count += 1
            return False
        rationale = (
            f"Cross-currency: {internal.currency} {internal.amount_minor} vs "
            f"{external.currency} {external.amount_minor} minor units; INR "
            f"{details['internal_inr_minor']} vs {details['external_inr_minor']}; variance "
            f"{details['variance_percent']}% within {details['tolerance_percent']}%."
        )
        result = MatchResult(
            run_id=self._run_id,
            match_type="CROSS_CURRENCY",
            status="AUTO_MATCHED",
            confidence=_CONFIDENCE,
            internal_transaction_id=internal.id,
            external_transaction_id=external.id,
            field_scores={"reference": 1.0, "fx_amount": 1.0},
            matched_fields={"normalised_reference": internal.normalised_reference, "fx": details},
            hard_constraints_passed=True,
            rule_id=_RULE_ID,
            candidate_count=1,
            rationale=rationale,
            matched_on_date=external.txn_date,
            weights_version=None,
        )
        self._session.add(result)
        self._session.flush()
        self._claims.finalise(internal_claim, result.id)
        self._claims.finalise(external_claim, result.id)
        internal.match_status = "MATCHED"
        external.match_status = "MATCHED"
        outcome.matched_count += 1
        outcome.match_result_ids.append(result.id)
        if self._audit is not None and self._now is not None:
            self._audit.append(
                chain_id=MATCHING_CHAIN,
                actor_type="SYSTEM",
                actor_id="cross-currency-matcher",
                action_type=AuditActionType.MATCH,
                occurred_at=self._now,
                affected_records={
                    "match_result_id": str(result.id),
                    "internal_transaction_id": str(internal.id),
                    "external_transaction_id": str(external.id),
                },
                after_state={
                    "match_type": "CROSS_CURRENCY",
                    "status": "AUTO_MATCHED",
                    "rule_id": _RULE_ID,
                    "fx": details,
                },
                rationale=rationale,
            )
        return True
