"""Pipeline benchmark on generated sample files, against the A6.1 targets.

    TEST_DATABASE_URL=... python -m recon.cli.benchmark --scale 20 \\
        --output docs/benchmarks/pipeline/scale-20.json

Refuses any database whose name does not end in _test; all work is rolled back."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from recon.bench.pipeline_bench import TARGETS_PER_MINUTE, run_pipeline_benchmark
from recon.samples.generator import DEFAULT_SEED, build_sample_set


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scale", type=int, default=10)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    url = args.database_url or os.environ.get("TEST_DATABASE_URL")
    if not url:
        print("set TEST_DATABASE_URL or pass --database-url", file=sys.stderr)
        return 2
    if not (make_url(url).database or "").endswith("_test"):
        print("refusing to run: the database name must end in _test", file=sys.stderr)
        return 2

    started = time.perf_counter()
    sample = build_sample_set(args.seed, args.scale)
    generation = time.perf_counter() - started
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                report = run_pipeline_benchmark(session, sample)
        finally:
            transaction.rollback()

    data = report.to_dict()
    data["generation_seconds"] = round(generation, 3)
    data["files"] = len(sample.files)
    data["scenarios"] = len(sample.scenarios)
    print(json.dumps(data, indent=2, sort_keys=True))
    for fmt, target in TARGETS_PER_MINUTE.items():
        actual = (
            data["ingest_per_minute"]
            if fmt == "ALL"
            else data["parse"].get(fmt, {}).get("per_minute", 0)
        )
        verdict = "meets" if actual >= target else "BELOW"
        label = "end-to-end ingestion" if fmt == "ALL" else f"{fmt} parsing"
        print(f"{label}: {actual:,}/min vs target {target:,}/min: {verdict}")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
