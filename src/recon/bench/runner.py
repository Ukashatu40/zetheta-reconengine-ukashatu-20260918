# src/recon/bench/runner.py
"""Runs the real matching pipeline over a synthetic dataset and scores it."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from recon.bench.dataset import SyntheticRecord, generate
from recon.bench.evaluate import Evaluation, MatchOutcome, evaluate
from recon.config.matching_loader import load_matching_config
from recon.matching.blocking.candidates import BlockingConfig
from recon.matching.orchestrator import MatchingOrchestrator
from recon.paths import config_dir
from recon.persistence.models import IngestionFile, MatchResult, NormalisedTransaction

BANK_CODE = "BENCH"
_CHUNK = 5000


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    size: int
    seed: int
    insert_seconds: float
    exact_seconds: float
    fuzzy_seconds: float
    orchestrator_seconds: float
    evaluation: Evaluation

    @property
    def auto_matches_per_second(self) -> float | None:
        matching = self.exact_seconds + self.fuzzy_seconds
        return self.evaluation.auto_total / matching if matching else None

    def to_dict(self) -> dict[str, object]:
        ev = self.evaluation
        return {
            "size": self.size,
            "seed": self.seed,
            "seconds": {
                "insert": self.insert_seconds,
                "exact": self.exact_seconds,
                "fuzzy": self.fuzzy_seconds,
                "orchestrator": self.orchestrator_seconds,
            },
            "auto_matches_per_second": self.auto_matches_per_second,
            "counts": {
                "internal": ev.internal_count,
                "external": ev.external_count,
                "true_pairs": ev.true_pairs,
                "auto": ev.auto_total,
                "auto_correct": ev.auto_correct,
                "false_auto_matches": ev.false_auto_matches,
                "review": ev.review_total,
                "review_correct": ev.review_correct,
                "exact_correct": ev.exact_correct,
            },
            "rates": {
                "auto_precision": ev.auto_precision,
                "auto_recall_of_true_pairs": ev.auto_recall_of_true_pairs,
                "auto_rate_of_externals": ev.auto_rate_of_externals,
                "exact_rate_of_externals": ev.exact_rate_of_externals,
                "exact_rate_of_true_pairs": ev.exact_rate_of_true_pairs,
            },
            "per_scenario": ev.per_scenario,
        }


def _row(record: SyntheticRecord, ingestion_file_id: uuid.UUID) -> dict[str, object]:
    return {
        "id": record.id,
        "txn_date": record.txn_date,
        "raw_transaction_id": record.id,
        "ingestion_file_id": ingestion_file_id,
        "source": record.source,
        "bank_code": BANK_CODE,
        "format_type": "CSV",
        "config_version": "bench.v1",
        "txn_id": record.reference,
        "amount": Decimal(record.amount_minor).scaleb(-2),
        "amount_minor": record.amount_minor,
        "currency": "INR",
        "currency_exponent": 2,
        "currency_source": "ENTRY_LEVEL",
        "direction": record.direction,
        "is_reversal": False,
        "txn_timestamp_utc": datetime(
            record.txn_date.year, record.txn_date.month, record.txn_date.day, 12, tzinfo=UTC
        ),
        "txn_timestamp_original": record.txn_date.strftime("%d-%m-%Y"),
        "source_timezone": "Asia/Kolkata",
        "normalised_reference": record.reference,
        "counterparty_name_normalised": record.counterparty,
        "match_status": "UNMATCHED",
    }


def _insert(session: Session, records: list[SyntheticRecord]) -> None:
    ingestion_file = IngestionFile(
        bank_code=BANK_CODE,
        format_type="CSV",
        original_filename="bench.csv",
        content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        size_bytes=1,
        ingested_by="bench",
    )
    session.add(ingestion_file)
    session.flush()
    for start in range(0, len(records), _CHUNK):
        chunk = records[start : start + _CHUNK]
        session.execute(insert(NormalisedTransaction), [_row(r, ingestion_file.id) for r in chunk])


def run_benchmark(session: Session, *, size: int, seed: int) -> BenchmarkReport:
    records = generate(size, seed)
    run_id = f"bench-{seed}-{size}-{uuid.uuid4().hex[:8]}"

    started = time.monotonic()
    _insert(session, records)
    insert_seconds = time.monotonic() - started

    result = MatchingOrchestrator(
        session,
        run_id,
        BlockingConfig(amount_bucket_width_minor=10_000, date_window_days=2),
        100,
        load_matching_config(config_dir() / "matching" / "weights.yaml"),
    ).run(BANK_CODE)

    rows = session.execute(
        select(
            MatchResult.internal_transaction_id,
            MatchResult.external_transaction_id,
            MatchResult.status,
            MatchResult.match_type,
        ).where(MatchResult.run_id == run_id)
    ).all()
    outcomes = [MatchOutcome(r[0], r[1], r[2], r[3]) for r in rows]

    return BenchmarkReport(
        size=size,
        seed=seed,
        insert_seconds=insert_seconds,
        exact_seconds=result.metrics.exact_duration_seconds,
        fuzzy_seconds=result.metrics.fuzzy_duration_seconds,
        orchestrator_seconds=result.metrics.total_duration_seconds,
        evaluation=evaluate(records, outcomes),
    )
