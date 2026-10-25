"""Seed the application database with demonstration data.

    python -m recon.cli.seed                    # golden-sized data, dated to the last Thursday
    python -m recon.cli.seed --scale 50         # a bigger volume, same planted cases
    python -m recon.cli.seed --anchor 2026-03-12 --no-reconcile

Everything goes through the real ingestion pipeline and runs, so the dashboard, the
reports and the audit trail are all genuinely populated. Statement files are
synthetic and deterministic (see recon.samples). It also creates one API key per
role (seed-viewer, seed-analyst, seed-admin) the first time, printing each ONCE.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from recon.persistence.models import ApiKey, IngestionFile
from recon.persistence.session import get_engine, get_session_factory
from recon.runs.settings import default_settings
from recon.samples.generator import DEFAULT_SEED, build_sample_set
from recon.samples.loader import load_files, reconcile_all
from recon.security.api_keys import generate_api_key, hash_api_key

_ROLES = ("VIEWER", "ANALYST", "ADMIN")
_THURSDAY = 3


def last_thursday(today: date) -> date:
    """The most recent Thursday strictly before today, so the data has a full day behind it."""
    day = today - timedelta(days=1)
    while day.weekday() != _THURSDAY:
        day -= timedelta(days=1)
    return day


def create_keys(session: Session) -> dict[str, str]:
    """One key per role, only for names that do not exist yet. Returns {name: raw key}."""
    created: dict[str, str] = {}
    for role in _ROLES:
        name = f"seed-{role.lower()}"
        if session.scalar(select(ApiKey.id).where(ApiKey.name == name)) is not None:
            continue
        raw = generate_api_key()
        session.add(ApiKey(key_hash=hash_api_key(raw), name=name, role=role))
        created[name] = raw
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed demonstration data")
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--anchor", type=date.fromisoformat, default=None, help="a Thursday")
    parser.add_argument("--no-reconcile", action="store_true", help="ingest only; do not match")
    args = parser.parse_args(argv)

    anchor = args.anchor or last_thursday(datetime.now(UTC).date())
    try:
        sample = build_sample_set(args.seed, args.scale, anchor)
    except ValueError as exc:
        print(f"cannot seed: {exc}", file=sys.stderr)
        return 2

    settings = default_settings()
    with get_session_factory(get_engine())() as session:
        if (session.scalar(select(func.count()).select_from(IngestionFile)) or 0) > 0:
            print(
                "the database already holds ingested files; refusing to seed on top of them",
                file=sys.stderr,
            )
            return 1
        summary = load_files(session, sample, settings, sample.as_of, "system:seed")
        keys = create_keys(session)
        session.commit()
        print(
            f"ingested {summary.files} files, {summary.records} records "
            f"({summary.parse_errors} malformed), {summary.fx_rates} exchange rates"
        )
        if not args.no_reconcile:
            runs = reconcile_all(
                session, settings, sample.as_of, sorted(settings.banks), "system:seed"
            )
            exceptions = sum(int(m.get("exceptions_created", 0)) for m in runs.values())
            print(f"reconciled {len(runs)} banks; {exceptions} exceptions raised")
        for name, raw in keys.items():
            print(f"{name}: {raw}")
        day = anchor.isoformat()
        print(f"data is dated {day}; try /api/v1/reports/daily?date={day}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
