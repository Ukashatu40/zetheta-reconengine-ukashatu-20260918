# ruff: noqa: T201, E501
# src/recon/bench/explain.py
"""CLI: python -m recon.bench.explain --size 5000 [--analyze-first] [--full]

Loads the synthetic dataset inside a rolled-back transaction, optionally runs
ANALYZE on the matching tables first, then EXPLAIN (ANALYZE, BUFFERS) on the
set-based exact statement. Same _test database guard as recon.bench.run. The
two bind parameters are replaced by literals because EXPLAIN is a utility
command; ANALYZE's statistics are transactional, so the rollback discards them.
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from recon.bench.dataset import generate
from recon.bench.runner import BANK_CODE, insert_records
from recon.matching.strategies.exact_set_based import PAIR_AND_CLAIM_SQL

_KEEP = (
    "Execution Time", "Planning Time", "Nested Loop", "Hash Join", "Hash Anti Join", "Hash Semi Join",
    "Merge Join", "CTE Scan", "Seq Scan", "Index Scan", "Index Only Scan", "Bitmap Heap Scan",
    "HashAggregate", "GroupAggregate", "Insert on", "Update on", "Sort",
)  # fmt: skip


def explain(session: Session, *, size: int, seed: int, analyze_first: bool) -> list[str]:
    insert_records(session, generate(size, seed))
    session.flush()
    if analyze_first:
        session.execute(text("ANALYZE normalised_transactions"))
        session.execute(text("ANALYZE match_claims"))
    sql = (
        str(PAIR_AND_CLAIM_SQL)
        .replace(":bank_code", f"'{BANK_CODE}'")
        .replace(":run_id", "'explain'")
    )
    rows = session.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sql))
    return [str(row[0]) for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EXPLAIN ANALYZE of the set-based exact statement")
    parser.add_argument("--size", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--analyze-first", action="store_true")
    parser.add_argument("--full", action="store_true", help="print the whole plan")
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
                lines = explain(
                    session, size=args.size, seed=args.seed, analyze_first=args.analyze_first
                )
        finally:
            transaction.rollback()

    print(
        f"statistics: {'refreshed with ANALYZE' if args.analyze_first else 'none (rows loaded in this transaction)'}"
    )
    for line in lines:
        if args.full or (any(key in line for key in _KEEP) and "never executed" not in line):
            print(line[:170])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
