# ruff: noqa: T201, E501
# src/recon/bench/run.py
"""CLI: python -m recon.bench.run --size 1000 --seed 42 [--output FILE]

Refuses any database whose name does not end in _test. All work happens in a
transaction that is rolled back, so nothing is left behind."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from recon.bench.profiling import StatementProfile
from recon.bench.runner import BenchmarkReport, run_benchmark


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _print(report: BenchmarkReport) -> None:
    data = report.to_dict()
    ev = report.evaluation
    print(
        f"size={report.size} seed={report.seed}  internal={ev.internal_count} external={ev.external_count}"
    )
    print(f"seconds: {data['seconds']}")
    print(f"auto matches/second (exact+fuzzy time): {_fmt(report.auto_matches_per_second)}")
    print(f"counts: {data['counts']}")
    print(f"rates:  { {k: _fmt(v) for k, v in data['rates'].items()} }")  # type: ignore[attr-defined]
    print("per scenario (by each entity's primary record):")
    for scenario, labels in sorted(ev.per_scenario.items()):
        print(f"  {scenario:24s} {dict(sorted(labels.items()))}")
    if report.profile is not None:
        p = report.profile
        per = (
            "n/a"
            if p.statements_per_persisted_result is None
            else f"{p.statements_per_persisted_result:.1f}"
        )
        print(
            f"profile: {p.statements} statements ({per} per persisted result); "
            f"{p.db_wait_seconds:.2f}s inside cursor.execute of {report.orchestrator_seconds:.2f}s total"
        )
        for kind, count in list(p.by_statement.items())[:12]:
            print(f"  {count:8d}  {kind}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconciliation benchmark on a synthetic dataset")
    parser.add_argument("--size", type=int, default=1000, help="number of entities")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--output", type=Path, default=None, help="write the report as JSON")
    parser.add_argument(
        "--profile", action="store_true", help="count SQL statements and DB wait time"
    )
    args = parser.parse_args(argv)

    url = args.database_url or os.environ.get("TEST_DATABASE_URL")
    if not url:
        print("set TEST_DATABASE_URL or pass --database-url", file=sys.stderr)
        return 2
    if not (make_url(url).database or "").endswith("_test"):
        print("refusing to run: the database name must end in _test", file=sys.stderr)
        return 2

    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                report = run_benchmark(
                    session,
                    size=args.size,
                    seed=args.seed,
                    profile=StatementProfile() if args.profile else None,
                )
        finally:
            transaction.rollback()

    _print(report)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
