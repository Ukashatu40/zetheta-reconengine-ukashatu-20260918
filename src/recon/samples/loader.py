# src/recon/samples/loader.py
"""Loads a SampleSet through the real pipeline: every file via IngestionPipeline, the
exchange-rate file via the FX loader, then one FULL run per bank.

Nothing here is test-only: the seed script and the golden gate both use it, so the
data a demo shows is the data the gate proves."""

from __future__ import annotations

import io
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from recon.fx.loader import parse_fx_csv
from recon.ingestion.pipeline import IngestionPipeline
from recon.persistence.models import ReconciliationRun
from recon.persistence.repositories.fx import FxRateRepository
from recon.runs.service import RunService
from recon.runs.settings import RunSettings
from recon.samples.generator import SampleSet


@dataclass(slots=True)
class LoadSummary:
    files: int = 0
    records: int = 0
    parse_errors: int = 0
    fx_rates: int = 0
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_files(
    session: Session, sample: SampleSet, settings: RunSettings, now: datetime, actor: str
) -> LoadSummary:
    """Ingests the FX file, then every ledger file, then every bank statement."""
    summary = LoadSummary()
    fx = parse_fx_csv(io.StringIO(sample.fx_rates_csv))
    summary.fx_rates, _ = FxRateRepository(session).insert_many(fx.rates)

    ordered = sorted(sample.files, key=lambda f: (f.source != "INTERNAL", f.bank, f.name))
    pipeline = IngestionPipeline(session, settings, actor)
    with tempfile.TemporaryDirectory(prefix="recon-sample-") as tmp:
        for f in ordered:
            path = Path(tmp) / f"{uuid.uuid4().hex}{Path(f.name).suffix}"
            path.write_text(f.content, encoding="utf-8")
            outcome = pipeline.process(
                path=path,
                display_name=f.name,
                sha256=f.sha256,
                size_bytes=len(f.content.encode()),
                config=settings.banks[f.bank],
                format_type=f.format_type,
                source=f.source,
                now=now,
            )
            summary.files += 1
            summary.records += outcome.result.record_count
            summary.parse_errors += outcome.result.error_count
    session.flush()
    return summary


def reconcile_all(
    session: Session, settings: RunSettings, now: datetime, banks: list[str], actor: str
) -> dict[str, dict[str, Any]]:
    """One FULL run per bank, in the order given. Returns each run's metrics."""
    results: dict[str, dict[str, Any]] = {}
    started = time.monotonic()
    # Business time starts at `now` and advances with the wall clock, so run durations (and the
    # dashboard's average processing time) are real rather than zero.
    service = RunService(
        session, settings, lambda: now + timedelta(seconds=time.monotonic() - started)
    )
    for bank in banks:
        begun = service.start(
            bank_code=bank,
            idempotency_key=f"sample:{bank}:{uuid.uuid4().hex[:12]}",
            requested_by=actor,
        )
        session.commit()
        service.execute(begun.run.id)
        session.commit()
        run = session.get(ReconciliationRun, begun.run.id)
        assert run is not None
        if run.status != "COMPLETED":
            raise RuntimeError(f"run for {bank} ended {run.status}: {run.error_summary}")
        results[bank] = run.metrics or {}
    return results
