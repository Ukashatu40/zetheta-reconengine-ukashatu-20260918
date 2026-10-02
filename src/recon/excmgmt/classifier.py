# src/recon/excmgmt/classifier.py
"""ExceptionClassifier (R61): turns quarantined rows and unmatched
transactions into routed, persisted exceptions.

Detects seven categories: FORMAT_ERROR, MISSING_INTERNAL, MISSING_EXTERNAL,
DIRECTION_REVERSAL, CURRENCY_MISMATCH, AMOUNT_MISMATCH, DATE_MISMATCH.
The rest need duplicate detection, split/netted/FX matching, or
statement-age data that does not exist yet.

Idempotent via dedupe_key. Audit events are not written (no AuditLogger
yet). Value thresholds use INR amounts only (no FX rates yet).
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

from recon.domain.enums import ExceptionCategory
from recon.excmgmt.routing import ExceptionContext, route
from recon.excmgmt.taxonomy import TaxonomyConfig
from recon.persistence.models import (
    ExceptionEvent,
    IngestionFile,
    NormalisedTransaction,
    RawTransaction,
    ReconException,
)
from recon.persistence.repositories.matching import MatchingRepository

_ACTOR = "system:classifier"
_KEY_CHUNK = 5000
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
    ExceptionCategory.DATE_MISMATCH: ("Check booking versus value dates and settlement timing."),
}


@dataclass(frozen=True, slots=True)
class ClassifierOutcome:
    created_count: int
    skipped_existing_count: int
    created_by_category: dict[str, int] = field(default_factory=dict)


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
    ingestion_file_id: uuid.UUID | None = None


class ExceptionClassifier:
    def __init__(self, session: Session, taxonomy: TaxonomyConfig) -> None:
        self._session = session
        self._taxonomy = taxonomy

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

    def classify_unmatched(self, bank_code: str, run_id: str, now: datetime) -> ClassifierOutcome:
        repository = MatchingRepository(self._session)
        internals = repository.find_unmatched(bank_code, "INTERNAL")
        externals = repository.find_unmatched(bank_code, "EXTERNAL")
        candidates = self._unmatched_candidates(bank_code, internals, externals)
        return self._persist(candidates, run_id, now, batch_count=len(candidates))

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
                        """a systemic format change is more likely than that many independent
                        bad rows."""
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
    ) -> list[_Candidate]:
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
            candidate = _pair_candidate(bank_code, internal, external)
            if candidate is None:
                continue
            paired_internals.add(internal.id)
            paired_externals.add(external.id)
            candidates.append(candidate)

        for external in externals:
            if external.id not in paired_externals:
                candidates.append(_missing_candidate(ExceptionCategory.MISSING_INTERNAL, external))
        for internal in internals:
            if internal.id not in paired_internals:
                candidates.append(_missing_candidate(ExceptionCategory.MISSING_EXTERNAL, internal))
        return candidates

    # -- persistence -------------------------------------------------------

    def _persist(
        self, candidates: list[_Candidate], run_id: str | None, now: datetime, *, batch_count: int
    ) -> ClassifierOutcome:
        existing = self._existing_keys([c.dedupe_key for c in candidates])
        created: Counter[str] = Counter()
        skipped = 0
        events: list[ExceptionEvent] = []

        for candidate in candidates:
            if candidate.dedupe_key in existing:
                skipped += 1
                continue
            decision = route(
                ExceptionContext(
                    category=candidate.category,
                    amount_inr_minor=candidate.amount_inr_minor,
                    amount_difference_minor=candidate.amount_difference_minor,
                    days_offset=candidate.days_offset,
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

        # Exceptions flush first: events carry a foreign key and there is no
        # relationship() to order the inserts for us.
        self._session.flush()
        self._session.add_all(events)
        self._session.flush()
        return ClassifierOutcome(sum(created.values()), skipped, dict(created))

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


def _error_codes(row: RawTransaction) -> str:
    errors = (row.parse_errors or {}).get("errors", [])
    return ", ".join(sorted({e["code"] for e in errors})) or "unspecified"


def _inr_minor(txn: NormalisedTransaction) -> int | None:
    return txn.amount_minor if txn.currency == "INR" else None


def _missing_candidate(category: ExceptionCategory, txn: NormalisedTransaction) -> _Candidate:
    side = (
        "bank statement but not in the internal ledger"
        if category is ExceptionCategory.MISSING_INTERNAL
        else ("internal ledger but not in the bank statement")
    )
    return _Candidate(
        category=category,
        dedupe_key=f"{category.value}:txn:{txn.id}",
        bank_code=txn.bank_code,
        affected_records={"normalised_transaction_ids": [str(txn.id)]},
        rationale=f"Reference {txn.normalised_reference or '(none)'} is in the {side}.",
        amount=txn.amount,
        currency=txn.currency,
        amount_inr_minor=_inr_minor(txn),
    )


def _pair_candidate(
    bank_code: str, internal: NormalisedTransaction, external: NormalisedTransaction
) -> _Candidate | None:
    category: ExceptionCategory
    difference: int | None = None
    days: int | None = None

    if internal.direction != external.direction:
        if internal.amount_minor != external.amount_minor or internal.currency != external.currency:
            return None  # opposite direction AND a different amount: not confidently a reversal
        category = ExceptionCategory.DIRECTION_REVERSAL
        why = f"direction {internal.direction} internally but {external.direction} at the bank"
    elif internal.currency != external.currency:
        category = ExceptionCategory.CURRENCY_MISMATCH
        why = f"currency {internal.currency} internally but {external.currency} at the bank"
    elif internal.amount_minor != external.amount_minor:
        category = ExceptionCategory.AMOUNT_MISMATCH
        difference = abs(internal.amount_minor - external.amount_minor)
        why = f"amounts differ by {difference} minor unit(s)"
    elif internal.txn_date != external.txn_date:
        category = ExceptionCategory.DATE_MISMATCH
        days = abs((internal.txn_date - external.txn_date).days)
        why = f"dates differ by {days} day(s)"
    else:
        return None  # identical on every compared field: not an exception

    return _Candidate(
        category=category,
        dedupe_key=f"{category.value}:pair:{internal.id}:{external.id}",
        bank_code=bank_code,
        affected_records={
            "internal_transaction_id": str(internal.id),
            "external_transaction_id": str(external.id),
        },
        rationale=f"Same reference {external.normalised_reference}; {why}.",
        amount=external.amount,
        currency=external.currency,
        amount_inr_minor=_inr_minor(external),
        amount_difference_minor=difference,
        days_offset=days,
    )
