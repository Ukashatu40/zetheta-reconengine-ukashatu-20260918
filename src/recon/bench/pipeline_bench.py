# src/recon/bench/pipeline_bench.py
"""Pipeline benchmark against the A6.1 targets, on the generated sample files.

Measures, separately and honestly:
  * parse-only throughput per format (the parsers alone, no database)
  * end-to-end ingestion (parse, mask, store, normalise, classify, scan) records/minute
  * matching: the exact and fuzzy stages and the whole run, per bank
  * peak resident memory of the process

Single runs on one machine; no figure here is a guarantee of anything but that run.
"""

from __future__ import annotations

import io
import resource
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from recon.config.registry import load_bank_configs
from recon.ingestion.parsers.camt053.parser import CAMT053Parser
from recon.ingestion.parsers.csv_parser import CSVParser
from recon.ingestion.parsers.mt940.state_machine import MT940Parser
from recon.runs.settings import default_settings
from recon.samples.generator import SampleFile, SampleSet
from recon.samples.loader import load_files, reconcile_all

# A6.1: records per minute
TARGETS_PER_MINUTE = {"CSV": 150_000, "MT940": 50_000, "CAMT053": 30_000, "ALL": 100_000}
_MIN_MEASURABLE_SECONDS = 1e-6


@dataclass(slots=True)
class Throughput:
    records: int = 0
    seconds: float = 0.0

    @property
    def per_minute(self) -> float:
        return self.records / max(self.seconds, _MIN_MEASURABLE_SECONDS) * 60


@dataclass(slots=True)
class BenchReport:
    scale: int
    parse: dict[str, Throughput] = field(default_factory=dict)
    ingest_records: int = 0
    ingest_seconds: float = 0.0
    run_seconds: dict[str, float] = field(default_factory=dict)
    exact_seconds: float = 0.0
    fuzzy_seconds: float = 0.0
    exact_matched: int = 0
    peak_rss_mb: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["parse"] = {
            fmt: {
                "records": t.records,
                "seconds": round(t.seconds, 4),
                "per_minute": round(t.per_minute),
            }
            for fmt, t in self.parse.items()
        }
        data["ingest_per_minute"] = round(self.ingest_records / max(self.ingest_seconds, 1e-6) * 60)
        data["targets_per_minute"] = TARGETS_PER_MINUTE
        data["exact_matches_per_second"] = round(self.exact_matched / max(self.exact_seconds, 1e-6))
        for key in ("ingest_seconds", "exact_seconds", "fuzzy_seconds"):
            data[key] = round(data[key], 3)
        data["run_seconds"] = {b: round(s, 3) for b, s in self.run_seconds.items()}
        data["peak_rss_mb"] = round(self.peak_rss_mb, 1)
        return data


def _parse(f: SampleFile) -> int:
    config = load_bank_configs()[f.bank]
    if f.format_type == "CSV":
        return sum(1 for _ in CSVParser().parse(io.StringIO(f.content), config))
    if f.format_type == "MT940":
        return sum(1 for _ in MT940Parser().parse(io.StringIO(f.content), config))
    return sum(1 for _ in CAMT053Parser().parse(io.BytesIO(f.content.encode()), config))


def parse_throughput(sample: SampleSet, repeat: int = 3) -> dict[str, Throughput]:
    """The parsers alone, best of `repeat` passes over the files of each format."""
    result: dict[str, Throughput] = {}
    for fmt in ("CSV", "MT940", "CAMT053"):
        files = [f for f in sample.files if f.format_type == fmt]
        best: Throughput | None = None
        for _ in range(repeat):
            started = time.perf_counter()
            records = sum(_parse(f) for f in files)
            elapsed = time.perf_counter() - started
            if best is None or elapsed < best.seconds:
                best = Throughput(records, elapsed)
        if best is not None:
            result[fmt] = best
    return result


def _peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def run_pipeline_benchmark(session: Session, sample: SampleSet) -> BenchReport:
    report = BenchReport(scale=sample.scale)
    report.parse = parse_throughput(sample)

    settings = default_settings()
    started = time.perf_counter()
    summary = load_files(session, sample, settings, sample.as_of, "system:bench")
    report.ingest_seconds = time.perf_counter() - started
    report.ingest_records = summary.records

    for bank in sorted(settings.banks):
        started = time.perf_counter()
        metrics = reconcile_all(session, settings, sample.as_of, [bank], "system:bench")[bank]
        report.run_seconds[bank] = time.perf_counter() - started
        durations = metrics.get("duration_seconds", {})
        report.exact_seconds += float(durations.get("exact", 0.0))
        report.fuzzy_seconds += float(durations.get("fuzzy", 0.0))
        report.exact_matched += int(metrics.get("exact_matched", 0))
    report.peak_rss_mb = _peak_rss_mb()
    return report
