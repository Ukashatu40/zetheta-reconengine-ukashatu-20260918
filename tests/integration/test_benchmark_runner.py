# tests/integration/test_benchmark_runner.py
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from recon.bench.dataset import generate
from recon.bench.profiling import StatementProfile
from recon.bench.runner import run_benchmark


def test_a_small_benchmark_runs_the_real_pipeline_with_no_false_auto_matches(
    db_session: Session,
) -> None:
    report = run_benchmark(db_session, size=200, seed=7)
    ev = report.evaluation

    assert ev.internal_count + ev.external_count == len(generate(200, 7))
    assert (
        sum(sum(labels.values()) for labels in ev.per_scenario.values()) == 200
    )  # one primary per entity
    assert ev.exact_correct > 0
    assert ev.false_auto_matches == 0
    assert report.exact_seconds >= 0 and report.fuzzy_seconds >= 0


def test_a_profile_counts_only_statements_inside_its_window(db_session: Session) -> None:
    profile = StatementProfile()
    db_session.execute(text("SELECT 1"))  # before attach
    profile.attach(db_session.connection())
    try:
        db_session.execute(text("SELECT 2"))
        db_session.execute(text("SELECT 3"))
    finally:
        profile.detach()
    db_session.execute(text("SELECT 4"))  # after detach

    assert sum(profile.counts.values()) == 2
    assert profile.db_seconds > 0
    profile.detach()  # detaching twice is harmless


def test_a_profile_cannot_be_attached_twice(db_session: Session) -> None:
    profile = StatementProfile()
    profile.attach(db_session.connection())
    try:
        with pytest.raises(RuntimeError, match="already attached"):
            profile.attach(db_session.connection())
    finally:
        profile.detach()


def test_profiled_benchmark_reports_statements_per_persisted_result(db_session: Session) -> None:
    report = run_benchmark(db_session, size=100, seed=3, profile=StatementProfile())

    assert report.profile is not None
    assert report.profile.statements > 0
    assert report.profile.statements_per_persisted_result is not None
    assert any(kind.startswith("INSERT match_results") for kind in report.profile.by_statement)
