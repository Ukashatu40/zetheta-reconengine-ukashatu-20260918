# src/recon/excmgmt/classifier.py
"""ExceptionClassifier (R61): turns quarantined rows, unmatched leftovers, review-band
pairs, matched pairs and group matches into routed, persisted exceptions.

Detects all 19 categories:
  * file level: FORMAT_ERROR
  * leftovers: MISSING_INTERNAL / MISSING_EXTERNAL, STALE_TRANSACTION,
    SETTLEMENT_DELAY, REVERSAL_PENDING, REGULATORY_HOLD, DUPLICATE_INTERNAL /
    DUPLICATE_EXTERNAL, and, for a pair sharing a reference, whatever
    pair_analysis finds (DIRECTION_REVERSAL, CURRENCY_MISMATCH, FX_VARIANCE,
    REFERENCE_TRUNCATED, FEE_DEDUCTION, AMOUNT_MISMATCH, TIMEZONE_OFFSET,
    DATE_MISMATCH)
  * review-band pairs: the same pair analysis, so a truncated reference or a
    fee-shaped gap is named instead of sitting anonymously in the review queue
  * matched pairs: dates, reference cuts and settlement timing that survive a match
  * split and netted groups: PARTIAL_MATCH and NETTED_SETTLEMENT records

Idempotent via dedupe_key. Value thresholds use the INR-equivalent amount (AE-07)
when an FX service is supplied, and the cumulative INR value per bank per day
feeds the Tier 3 rule of A4.2.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.orm import Session, aliased

from recon.audit.chains import EXCEPTIONS_CHAIN
from recon.audit.logger import AuditLogger
from recon.calendar import default_calendar
from recon.config.models import BankConfig
from recon.domain.enums import AuditActionType, ExceptionCategory
from recon.excmgmt.pair_analysis import (
    PairContext,
    PairFinding,
    analyse_pair,
    has_regulatory_hold,
    is_posted_reversal,
    keyword_pattern,
)
from recon.excmgmt.routing import ExceptionContext, RoutingDecision, route
from recon.excmgmt.taxonomy import TaxonomyConfig
from recon.fx.convert import to_inr_minor
from recon.fx.service import FxService
from recon.persistence.models import (
    ExceptionEvent,
    IngestionFile,
    MatchResult,
    NormalisedTransaction,
    RawTransaction,
    ReconException,
)
from recon.persistence.repositories.matching import MatchingRepository

_ACTOR = "system:classifier"
_KEY_CHUNK = 5000
_DUP_KEY_CHUNK = 1000  # six bind parameters per key; stays well under the driver limit
_FORMAT_ERROR_SAMPLE_LINES = 100

_SUGGESTED_RESOLUTION: dict[ExceptionCategory, str] = {
    ExceptionCategory.FORMAT_ERROR: (
        "Inspect the source file and bank configuration; correct and re-ingest."
    ),
    ExceptionCategory.MISSING_INTERNAL: (
        "Find the transaction in the internal ledger; post it if it is genuine."
    ),
    ExceptionCategory.MISSING_EXTERNAL: (
        "Check the bank statement and settlement timing; chase the bank if overdue."
    ),
    ExceptionCategory.DIRECTION_REVERSAL: (
        "Escalate to compliance: one side booked the debit/credit direction wrongly."
    ),
    ExceptionCategory.CURRENCY_MISMATCH: (
        "Confirm the transaction currency with both sources and correct the wrong side."
    ),
    ExceptionCategory.AMOUNT_MISMATCH: (
        "Compare the amounts; correct the wrong side or record a fee or adjustment."
    ),
    ExceptionCategory.DATE_MISMATCH: "Check booking versus value dates and settlement timing.",
    ExceptionCategory.DUPLICATE_INTERNAL: (
        "Void the duplicate ledger entry and link it to the original."
    ),
    ExceptionCategory.DUPLICATE_EXTERNAL: (
        "Confirm with the bank whether the settlement was processed twice; do not double-count."
    ),
    ExceptionCategory.STALE_TRANSACTION: (
        "Investigate why it is still unmatched past the window; "
        "chase the bank or write off with approval."
    ),
    ExceptionCategory.PARTIAL_MATCH: (
        "One internal transaction was settled in several parts that sum to it; "
        "confirm the grouping."
    ),
    ExceptionCategory.NETTED_SETTLEMENT: (
        "Several internal transactions were netted into one bank entry that sums to them; "
        "confirm the grouping."
    ),
    ExceptionCategory.FEE_DEDUCTION: (
        "Post an adjustment for the bank's fee so the ledger reflects what was settled."
    ),
    ExceptionCategory.FX_VARIANCE: (
        "Compare the booking rate with the RBI reference rate; claim the difference "
        "from the bank or write it off with approval."
    ),
    ExceptionCategory.REFERENCE_TRUNCATED: (
        "The bank cut the reference; match on the shared prefix and confirm the rest."
    ),
    ExceptionCategory.TIMEZONE_OFFSET: (
        "The dates differ only because of timezone conversion; re-match on the adjusted date."
    ),
    ExceptionCategory.REVERSAL_PENDING: (
        "A reversal is posted internally but not yet settled by the bank; "
        "chase the bank for the reversing entry."
    ),
    ExceptionCategory.REGULATORY_HOLD: (
        "Hold for compliance review; do not release or write off without sign-off."
    ),
    ExceptionCategory.SETTLEMENT_DELAY: (
        "Settlement ran past the cycle the bank is bound to; raise it with the bank "
        "and track against the RBI timeline."
    ),
}

# A4.1 #17-19 and A7.1 are single-record: they describe one transaction, not a pair.
_HOLD_RATIONALE = "The narration marks reference {ref} as held for compliance review."


@dataclass(frozen=True, slots=True)
class ClassifierOutcome:
    created_count: int
    skipped_existing_count: int
    created_by_category: dict[str, int] = field(default_factory=dict)

    def __add__(self, other: ClassifierOutcome) -> ClassifierOutcome:
        merged: Counter[str] = Counter(self.created_by_category)
        merged.update(other.created_by_category)
        return ClassifierOutcome(
            self.created_count + other.created_count,
            self.skipped_existing_count + other.skipped_existing_count,
            dict(merged),
        )


@dataclass(frozen=True, slots=True)
class _Candidate:
    category: ExceptionCategory
    dedupe_key: str
    bank_code: str
    affected_records: dict[str, Any]
    rationale: str
    amount: Decimal | None = None
    currency: str | None = None
    amount_inr_minor: int | None = None
    amount_difference_minor: int | None = None
    days_offset: int | None = None
    fuzzy_confidence: Decimal | None = None
    fx_variance_percent: Decimal | None = None
    value_date: date | None = None
    ingestion_file_id: uuid.UUID | None = None


class ExceptionClassifier:
    def __init__(
        self,
        session: Session,
        taxonomy: TaxonomyConfig,
        audit: AuditLogger,
        *,
        banks: Mapping[str, BankConfig] | None = None,
        fx: FxService | None = None,
    ) -> None:
        self._session = session
        self._taxonomy = taxonomy
        self._audit = audit
        self._banks = banks
        self._fx = fx

    # -- public entry points -------------------------------------------------

    def classify_ingestion_file(
        self, ingestion_file_id: uuid.UUID, now: datetime
    ) -> ClassifierOutcome:
        ingestion_file = self._session.get(IngestionFile, ingestion_file_id)
        if ingestion_file is None:
            raise LookupError(f"no ingestion file {ingestion_file_id}")
        rows = (
            self._session.query(RawTransaction)
            .filter(
                RawTransaction.ingestion_file_id == ingestion_file_id,
                RawTransaction.parse_status == "QUARANTINED",
            )
            .order_by(RawTransaction.source_line_no, RawTransaction.id)
            .all()
        )
        candidates = self._format_error_candidates(ingestion_file, rows)
        return self._persist(candidates, None, now, batch_count=len(rows))

    def classify_run(
        self,
        bank_code: str,
        run_id: str,
        now: datetime,
        *,
        as_of_date: date | None = None,
        stale_after_days: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> ClassifierOutcome:
        """Everything a run can raise, in one pass: leftovers first, then the pairs
        and groups the run produced."""
        return (
            self.classify_unmatched(
                bank_code,
                run_id,
                now,
                as_of_date=as_of_date,
                stale_after_days=stale_after_days,
                date_from=date_from,
                date_to=date_to,
            )
            + self.classify_pending_review(bank_code, run_id, now)
            + self.classify_matched(bank_code, run_id, now)
            + self.classify_groups(bank_code, run_id, now)
        )

    def classify_unmatched(
        self,
        bank_code: str,
        run_id: str,
        now: datetime,
        *,
        as_of_date: date | None = None,
        stale_after_days: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> ClassifierOutcome:
        if (as_of_date is None) != (stale_after_days is None):
            raise ValueError("as_of_date and stale_after_days must be given together")
        duplicates, duplicate_ids = self._duplicate_candidates(bank_code, date_from, date_to)
        repository = MatchingRepository(self._session)
        internals = [
            t
            for t in repository.find_unmatched(
                bank_code, "INTERNAL", date_from=date_from, date_to=date_to
            )
            if t.id not in duplicate_ids
        ]
        externals = [
            t
            for t in repository.find_unmatched(
                bank_code, "EXTERNAL", date_from=date_from, date_to=date_to
            )
            if t.id not in duplicate_ids
        ]
        candidates = duplicates + self._unmatched_candidates(
            bank_code,
            internals,
            externals,
            as_of_date=as_of_date,
            stale_after_days=stale_after_days,
        )
        return self._persist(candidates, run_id, now, batch_count=len(candidates))

    def classify_pending_review(
        self, bank_code: str, run_id: str, now: datetime
    ) -> ClassifierOutcome:
        """Names the review-band pairs the run proposed. A pair the analysis finds
        nothing wrong with (an identical pair, e.g. a sequential-reference decoy)
        stays in the review queue without an exception."""
        i, e = aliased(NormalisedTransaction), aliased(NormalisedTransaction)
        rows = self._session.execute(
            select(MatchResult, i, e)
            .join(i, i.id == MatchResult.internal_transaction_id)
            .join(e, e.id == MatchResult.external_transaction_id)
            .where(
                MatchResult.run_id == run_id,
                MatchResult.status == "PENDING_REVIEW",
                MatchResult.match_group_id.is_(None),
                i.bank_code == bank_code,
            )
            .order_by(MatchResult.created_at, MatchResult.id)
        ).all()
        ctx = self._context(bank_code)
        candidates: list[_Candidate] = []
        for result, internal, external in rows:
            for finding in analyse_pair(
                internal, external, ctx, matched=False, confidence=result.confidence
            )[:1]:
                candidates.append(
                    self._pair_candidate_from(
                        bank_code,
                        internal,
                        external,
                        finding,
                        key=f"{finding.category.value}:pair:{internal.id}:{external.id}",
                        match_result_id=result.id,
                    )
                )
        return self._persist(candidates, run_id, now, batch_count=len(candidates))

    def classify_matched(self, bank_code: str, run_id: str, now: datetime) -> ClassifierOutcome:
        """Findings that survive a match: dates, truncated references, settlement
        timing, compliance holds. Only pairs that could have one are loaded."""
        i, e = aliased(NormalisedTransaction), aliased(NormalisedTransaction)
        detection = self._taxonomy.detection
        conditions = [
            i.txn_date != e.txn_date,
            func.coalesce(e.settlement_date, e.txn_date) != i.txn_date,
            i.normalised_reference.is_distinct_from(e.normalised_reference),
        ]
        hold = keyword_pattern(detection.regulatory_hold_keywords)
        if hold is not None:
            posix = hold.pattern.replace("\\b", "\\y")  # PostgreSQL's word boundary
            for side in (i, e):
                conditions.append(
                    func.coalesce(side.narration_normalised, side.narration, "").op("~*")(posix)
                )
        rows = self._session.execute(
            select(MatchResult, i, e)
            .join(i, i.id == MatchResult.internal_transaction_id)
            .join(e, e.id == MatchResult.external_transaction_id)
            .where(
                MatchResult.run_id == run_id,
                MatchResult.status.in_(("AUTO_MATCHED", "CONFIRMED")),
                MatchResult.match_group_id.is_(None),
                i.bank_code == bank_code,
                or_(*conditions),
            )
            .order_by(MatchResult.created_at, MatchResult.id)
        ).all()
        ctx = self._context(bank_code)
        candidates = [
            self._pair_candidate_from(
                bank_code,
                internal,
                external,
                finding,
                key=f"{finding.category.value}:matched:{result.id}",
                match_result_id=result.id,
                already_matched=True,
            )
            for result, internal, external in rows
            for finding in analyse_pair(
                internal, external, ctx, matched=True, confidence=result.confidence
            )
        ]
        return self._persist(candidates, run_id, now, batch_count=len(candidates))

    def classify_groups(self, bank_code: str, run_id: str, now: datetime) -> ClassifierOutcome:
        """One PARTIAL_MATCH (split) or NETTED_SETTLEMENT (netted) record per group."""
        groups: dict[uuid.UUID, list[MatchResult]] = defaultdict(list)
        for result in self._session.scalars(
            select(MatchResult)
            .join(
                NormalisedTransaction,
                NormalisedTransaction.id == MatchResult.internal_transaction_id,
            )
            .where(
                MatchResult.run_id == run_id,
                MatchResult.match_group_id.is_not(None),
                NormalisedTransaction.bank_code == bank_code,
            )
            .order_by(MatchResult.created_at, MatchResult.id)
        ):
            assert result.match_group_id is not None
            groups[result.match_group_id].append(result)

        candidates: list[_Candidate] = []
        for group_id, results in groups.items():
            first = results[0]
            split = first.match_type == "SPLIT"
            single_id = uuid.UUID(first.matched_fields["single_transaction_id"])
            single = self._session.scalars(
                select(NormalisedTransaction).where(NormalisedTransaction.id == single_id)
            ).one()
            category = (
                ExceptionCategory.PARTIAL_MATCH if split else ExceptionCategory.NETTED_SETTLEMENT
            )
            members = first.matched_fields["member_transaction_ids"]
            candidates.append(
                _Candidate(
                    category=category,
                    dedupe_key=f"{category.value}:group:{group_id}",
                    bank_code=bank_code,
                    affected_records={
                        "match_group_id": str(group_id),
                        "match_result_ids": [str(r.id) for r in results],
                        "single_transaction_id": str(single_id),
                        "member_transaction_ids": members,
                        "already_matched": True,
                    },
                    rationale=first.rationale,
                    amount=single.amount,
                    currency=single.currency,
                    amount_inr_minor=self._inr_minor(single),
                    value_date=single.txn_date,
                )
            )
        return self._persist(candidates, run_id, now, batch_count=len(candidates))

    # -- context -------------------------------------------------------------

    def _context(self, bank_code: str) -> PairContext:
        bank = self._banks.get(bank_code) if self._banks else None
        return PairContext(
            detection=self._taxonomy.detection,
            calendar=default_calendar(bank.calendar if bank else "in"),
            bank=bank,
            fx=self._fx,
            fx_tolerance_percent=bank.fx_tolerance_percent if bank else Decimal("1.5"),
        )

    def _inr_minor(self, txn: NormalisedTransaction) -> int | None:
        """INR-equivalent in paise (AE-07). Non-INR amounts need a rate; without
        one the value rules simply do not apply to that record."""
        if txn.currency == "INR":
            return txn.amount_minor
        if self._fx is None:
            return None
        quote = self._fx.quote(txn.currency, txn.txn_date)
        if quote is None:
            return None
        return to_inr_minor(txn.amount_minor, txn.currency, quote.rate_inr)

    # -- candidate building ------------------------------------------------

    def _format_error_candidates(
        self, ingestion_file: IngestionFile, rows: list[RawTransaction]
    ) -> list[_Candidate]:
        if not rows:
            return []
        if len(rows) > self._taxonomy.escalation.systemic_exception_count:
            return [
                _Candidate(
                    category=ExceptionCategory.FORMAT_ERROR,
                    dedupe_key=f"FORMAT_ERROR:file:{ingestion_file.id}",
                    bank_code=ingestion_file.bank_code,
                    affected_records={
                        "total_count": len(rows),
                        "sample_line_numbers": [
                            r.source_line_no for r in rows[:_FORMAT_ERROR_SAMPLE_LINES]
                        ],
                    },
                    rationale=(
                        f"{len(rows)} rows of {ingestion_file.original_filename} failed parsing; "
                        "a systemic format change is more likely than that many independent "
                        "bad rows."
                    ),
                    ingestion_file_id=ingestion_file.id,
                )
            ]
        return [
            _Candidate(
                category=ExceptionCategory.FORMAT_ERROR,
                dedupe_key=f"FORMAT_ERROR:raw:{row.id}",
                bank_code=ingestion_file.bank_code,
                affected_records={
                    "raw_transaction_ids": [str(row.id)],
                    "line_numbers": [row.source_line_no],
                },
                rationale=f"Line {row.source_line_no} failed parsing: {_error_codes(row)}.",
                ingestion_file_id=ingestion_file.id,
            )
            for row in rows
        ]

    def _unmatched_candidates(
        self,
        bank_code: str,
        internals: list[NormalisedTransaction],
        externals: list[NormalisedTransaction],
        *,
        as_of_date: date | None,
        stale_after_days: int | None,
    ) -> list[_Candidate]:
        ctx = self._context(bank_code)
        by_reference: dict[str, list[NormalisedTransaction]] = {}
        for internal in internals:
            if internal.normalised_reference:
                by_reference.setdefault(internal.normalised_reference, []).append(internal)

        paired_internals: set[uuid.UUID] = set()
        paired_externals: set[uuid.UUID] = set()
        candidates: list[_Candidate] = []

        for external in externals:
            peers = by_reference.get(external.normalised_reference or "", [])
            if len(peers) != 1 or peers[0].id in paired_internals:
                continue  # none, or ambiguous: both sides fall back to MISSING
            internal = peers[0]
            findings = analyse_pair(internal, external, ctx, matched=False)
            if not findings:
                continue
            paired_internals.add(internal.id)
            paired_externals.add(external.id)
            finding = findings[0]
            candidates.append(
                self._pair_candidate_from(
                    bank_code,
                    internal,
                    external,
                    finding,
                    key=f"{finding.category.value}:pair:{internal.id}:{external.id}",
                )
            )

        for external in externals:
            if external.id not in paired_externals:
                candidates.append(
                    self._single_candidate(
                        ExceptionCategory.MISSING_INTERNAL,
                        external,
                        ctx,
                        as_of_date=as_of_date,
                        stale_after_days=stale_after_days,
                    )
                )
        for internal in internals:
            if internal.id not in paired_internals:
                candidates.append(
                    self._single_candidate(
                        ExceptionCategory.MISSING_EXTERNAL,
                        internal,
                        ctx,
                        as_of_date=as_of_date,
                        stale_after_days=stale_after_days,
                    )
                )
        return candidates

    def _pair_candidate_from(
        self,
        bank_code: str,
        internal: NormalisedTransaction,
        external: NormalisedTransaction,
        finding: PairFinding,
        *,
        key: str,
        match_result_id: uuid.UUID | None = None,
        already_matched: bool = False,
    ) -> _Candidate:
        affected: dict[str, Any] = {
            "internal_transaction_id": str(internal.id),
            "external_transaction_id": str(external.id),
        }
        if match_result_id is not None:
            affected["match_result_id"] = str(match_result_id)
        if already_matched:
            affected["already_matched"] = True
        if finding.details:
            affected["details"] = finding.details
        return _Candidate(
            category=finding.category,
            dedupe_key=key,
            bank_code=bank_code,
            affected_records=affected,
            rationale=f"Reference {external.normalised_reference}: {finding.why}.",
            amount=external.amount,
            currency=external.currency,
            amount_inr_minor=self._inr_minor(external),
            amount_difference_minor=finding.amount_difference_minor,
            days_offset=finding.days_offset,
            fuzzy_confidence=finding.fuzzy_confidence,
            fx_variance_percent=finding.fx_variance_percent,
            value_date=external.txn_date,
        )

    def _single_candidate(
        self,
        base: ExceptionCategory,
        txn: NormalisedTransaction,
        ctx: PairContext,
        *,
        as_of_date: date | None,
        stale_after_days: int | None,
    ) -> _Candidate:
        """One unmatched record with no counterpart. A compliance hold outranks a
        pending reversal, which outranks staleness, which outranks a delay."""
        side = (
            "bank statement but not in the internal ledger"
            if base is ExceptionCategory.MISSING_INTERNAL
            else "internal ledger but not in the bank statement"
        )
        reference = txn.normalised_reference or "(none)"
        if has_regulatory_hold(txn, ctx.detection):
            category = ExceptionCategory.REGULATORY_HOLD
            rationale = _HOLD_RATIONALE.format(ref=reference)
        elif base is ExceptionCategory.MISSING_EXTERNAL and is_posted_reversal(txn, ctx.detection):
            category = ExceptionCategory.REVERSAL_PENDING
            rationale = (
                f"Reversal {reference} is posted in the internal ledger but the bank "
                "has not settled it."
            )
        elif _is_stale(txn, as_of_date, stale_after_days):
            category = ExceptionCategory.STALE_TRANSACTION
            rationale = (
                f"Past the reconciliation window and still unmatched: reference {reference} "
                f"is in the {side}."
            )
        elif (
            base is ExceptionCategory.MISSING_EXTERNAL
            and ctx.bank is not None
            and as_of_date is not None
            and ctx.calendar.business_days_between(txn.txn_date, as_of_date) > ctx.settlement_days
        ):
            category = ExceptionCategory.SETTLEMENT_DELAY
            age = ctx.calendar.business_days_between(txn.txn_date, as_of_date)
            rationale = (
                f"Reference {reference} is {age} business days old and the bank has not "
                f"settled it (cycle T+{ctx.settlement_days})."
            )
        else:
            category = base
            rationale = f"Reference {reference} is in the {side}."
        return _Candidate(
            category=category,
            dedupe_key=f"{category.value}:txn:{txn.id}",
            bank_code=txn.bank_code,
            affected_records={
                "normalised_transaction_ids": [str(txn.id)],
                "missing_side": base.value,
            },
            rationale=rationale,
            amount=txn.amount,
            currency=txn.currency,
            amount_inr_minor=self._inr_minor(txn),
            value_date=txn.txn_date,
        )

    # -- persistence -------------------------------------------------------

    def _persist(
        self, candidates: list[_Candidate], run_id: str | None, now: datetime, *, batch_count: int
    ) -> ClassifierOutcome:
        existing = self._existing_keys([c.dedupe_key for c in candidates])
        created: Counter[str] = Counter()
        skipped = 0
        events: list[ExceptionEvent] = []
        audited: list[tuple[uuid.UUID, _Candidate, RoutingDecision]] = []
        daily = _cumulative_daily_inr(c for c in candidates if c.dedupe_key not in existing)

        for candidate in candidates:
            if candidate.dedupe_key in existing:
                skipped += 1
                continue
            decision = route(
                ExceptionContext(
                    category=candidate.category,
                    amount_inr_minor=candidate.amount_inr_minor,
                    cumulative_daily_inr_minor=daily.get(
                        (candidate.bank_code, candidate.value_date)
                    ),
                    amount_difference_minor=candidate.amount_difference_minor,
                    days_offset=candidate.days_offset,
                    fuzzy_confidence=candidate.fuzzy_confidence,
                    fx_variance_percent=candidate.fx_variance_percent,
                    bank_batch_exception_count=batch_count,
                ),
                self._taxonomy,
                now,
            )
            exception_id = uuid.uuid4()
            self._session.add(
                ReconException(
                    id=exception_id,
                    dedupe_key=candidate.dedupe_key,
                    category=candidate.category.value,
                    severity=decision.severity.value,
                    status="OPEN",
                    assigned_tier=decision.tier.value,
                    bank_code=candidate.bank_code,
                    run_id=run_id,
                    ingestion_file_id=candidate.ingestion_file_id,
                    affected_records=candidate.affected_records,
                    financial_impact_amount=candidate.amount,
                    financial_impact_currency=candidate.currency,
                    financial_impact_inr_minor=candidate.amount_inr_minor,
                    suggested_resolution=_SUGGESTED_RESOLUTION[candidate.category],
                    rationale=f"{candidate.rationale} Routing: {decision.rationale}.",
                    created_at=now,
                    sla_deadline=decision.sla_deadline,
                )
            )
            events.append(
                ExceptionEvent(
                    exception_id=exception_id,
                    event_type="CREATED",
                    to_tier=decision.tier.value,
                    actor=_ACTOR,
                    detail=(
                        f"{candidate.category.value} created, "
                        f"routed to tier {decision.tier.value}."
                    ),
                    occurred_at=now,
                )
            )
            created[candidate.category.value] += 1
            audited.append((exception_id, candidate, decision))

        # Exceptions flush first: events carry a foreign key and there is no
        # relationship() to order the inserts for us.
        self._session.flush()
        self._session.add_all(events)
        self._session.flush()
        self._audit_created(audited, now)
        return ClassifierOutcome(sum(created.values()), skipped, dict(created))

    def _audit_created(
        self, audited: list[tuple[uuid.UUID, _Candidate, RoutingDecision]], now: datetime
    ) -> None:
        for exception_id, candidate, decision in audited:
            self._audit.append(
                chain_id=EXCEPTIONS_CHAIN,
                actor_type="SYSTEM",
                actor_id="classifier",
                action_type=AuditActionType.EXCEPTION_CREATE,
                occurred_at=now,
                affected_records={"exception_id": str(exception_id), **candidate.affected_records},
                after_state={
                    "category": candidate.category.value,
                    "severity": decision.severity.value,
                    "tier": decision.tier.value,
                    "status": "OPEN",
                    "sla_deadline": decision.sla_deadline.isoformat(),
                },
                rationale=f"{candidate.rationale} Routing: {decision.rationale}.",
            )

    def _duplicate_candidate(
        self, bank_code: str, original: NormalisedTransaction, copy: NormalisedTransaction
    ) -> _Candidate:
        category = (
            ExceptionCategory.DUPLICATE_INTERNAL
            if copy.source == "INTERNAL"
            else ExceptionCategory.DUPLICATE_EXTERNAL
        )
        same_file = copy.ingestion_file_id == original.ingestion_file_id
        where = "the same file" if same_file else "a different ingestion file"
        return _Candidate(
            category=category,
            dedupe_key=f"{category.value}:txn:{copy.id}",
            bank_code=bank_code,
            affected_records={
                "normalised_transaction_ids": [str(copy.id)],
                "original_transaction_id": str(original.id),
                "same_ingestion_file": same_file,
            },
            rationale=(
                f"""Reference {copy.normalised_reference} with identical amount,
                currency, direction and date """
                f"already exists as transaction {original.id} ({where}, ingested first)."
            ),
            amount=copy.amount,
            currency=copy.currency,
            amount_inr_minor=self._inr_minor(copy),
            value_date=copy.txn_date,
        )

    def _existing_keys(self, keys: list[str]) -> set[str]:
        found: set[str] = set()
        for start in range(0, len(keys), _KEY_CHUNK):
            chunk = keys[start : start + _KEY_CHUNK]
            found.update(
                self._session.scalars(
                    select(ReconException.dedupe_key).where(ReconException.dedupe_key.in_(chunk))
                )
            )
        return found

    def _duplicate_candidates(
        self, bank_code: str, date_from: date | None = None, date_to: date | None = None
    ) -> tuple[list[_Candidate], set[uuid.UUID]]:
        """Reports every copy after the first (earliest ingestion, then line,
        then id) of a transaction sharing source, reference, amount,
        currency, direction and date. Ignores match status."""
        nt = NormalisedTransaction
        key_columns = (
            nt.source,
            nt.normalised_reference,
            nt.amount_minor,
            nt.currency,
            nt.direction,
            nt.txn_date,
        )
        in_range = [nt.bank_code == bank_code]
        if date_from is not None:
            in_range.append(nt.txn_date >= date_from)
        if date_to is not None:
            in_range.append(nt.txn_date <= date_to)
        keys = [
            tuple(row)
            for row in self._session.execute(
                select(*key_columns)
                .where(
                    *in_range,
                    nt.normalised_reference.is_not(None),
                    nt.normalised_reference != "",
                )
                .group_by(*key_columns)
                .having(func.count() > 1)
            )
        ]
        candidates: list[_Candidate] = []
        duplicate_ids: set[uuid.UUID] = set()
        for start in range(0, len(keys), _DUP_KEY_CHUNK):
            members = (
                self._session.query(nt)
                .join(IngestionFile, IngestionFile.id == nt.ingestion_file_id)
                .filter(
                    nt.bank_code == bank_code,
                    tuple_(*key_columns).in_(keys[start : start + _DUP_KEY_CHUNK]),
                )
                .order_by(IngestionFile.ingested_at, nt.source_line_no, nt.id)
                .all()
            )
            groups: dict[tuple[object, ...], list[NormalisedTransaction]] = {}
            for member in members:
                key = (member.source, member.normalised_reference, member.amount_minor,
                       member.currency, member.direction, member.txn_date)  # fmt: skip
                groups.setdefault(key, []).append(member)
            for group in groups.values():
                original, *copies = group
                for copy in copies:
                    duplicate_ids.add(copy.id)
                    candidates.append(self._duplicate_candidate(bank_code, original, copy))
        return candidates, duplicate_ids


def _error_codes(row: RawTransaction) -> str:
    errors = (row.parse_errors or {}).get("errors", [])
    return ", ".join(sorted({e["code"] for e in errors})) or "unspecified"


def _is_stale(
    txn: NormalisedTransaction, as_of_date: date | None, stale_after_days: int | None
) -> bool:
    if as_of_date is None or stale_after_days is None:
        return False
    return (as_of_date - txn.txn_date).days > stale_after_days


def _cumulative_daily_inr(candidates: Iterable[_Candidate]) -> dict[tuple[str, date | None], int]:
    """Total INR-equivalent value per (bank, day) across a batch: A4.2's
    cumulative daily threshold sends the day's exceptions to a senior manager."""
    totals: dict[tuple[str, date | None], int] = defaultdict(int)
    for candidate in candidates:
        if candidate.amount_inr_minor is not None and candidate.value_date is not None:
            totals[(candidate.bank_code, candidate.value_date)] += candidate.amount_inr_minor
    return dict(totals)
