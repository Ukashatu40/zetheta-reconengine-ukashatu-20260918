"""A7.2: any historical run must be reproducible from the same inputs, with identical results.

Two separate databases transactions each load the same sample data and run it; the outcome of
every match and exception, identified by transaction references and not by random ids, must be
identical."""

from __future__ import annotations

import hashlib
import json
import re

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from recon.persistence.models import MatchResult, NormalisedTransaction, ReconException
from recon.runs.settings import default_settings
from recon.samples.generator import DEFAULT_SEED, build_sample_set
from recon.samples.loader import load_files, reconcile_all

pytestmark = pytest.mark.golden

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _digest(session: Session) -> tuple[str, int, int]:
    refs = {
        str(t.id): f"{t.bank_code}|{t.source}|{t.normalised_reference}|{t.source_line_no}"
        for t in session.scalars(select(NormalisedTransaction))
    }
    matches = sorted(
        (
            refs[str(m.internal_transaction_id)],
            refs[str(m.external_transaction_id)],
            m.match_type,
            m.status,
            str(m.confidence),
        )
        for m in session.scalars(select(MatchResult))
    )
    exceptions = sorted(
        (
            e.bank_code,
            e.category,
            e.severity,
            e.assigned_tier,
            e.status,
            sorted(refs.get(i, "?") for i in _UUID.findall(json.dumps(e.affected_records))),
        )
        for e in session.scalars(select(ReconException))
    )
    blob = json.dumps([matches, exceptions], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest(), len(matches), len(exceptions)


def _run_once(engine: Engine) -> tuple[str, int, int]:
    sample = build_sample_set(DEFAULT_SEED, 1)
    settings = default_settings()
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                load_files(session, sample, settings, sample.as_of, "system:determinism")
                reconcile_all(session, settings, sample.as_of, sorted(settings.banks), "system")
                return _digest(session)
        finally:
            outer.rollback()


def test_the_same_inputs_give_identical_matches_and_exceptions(engine: Engine) -> None:
    first, second = _run_once(engine), _run_once(engine)
    assert first[1] > 100 and first[2] > 20  # the digest covers a real result set
    assert first == second
