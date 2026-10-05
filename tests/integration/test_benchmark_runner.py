# tests/integration/test_benchmark_runner.py
from __future__ import annotations

from sqlalchemy.orm import Session

from recon.bench.dataset import generate
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
