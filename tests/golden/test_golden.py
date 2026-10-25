"""The golden gate (A8.3): the planted dataset is regenerated, run through the real
pipeline, and every scenario's outcome is compared with the manifest. Any deviation
fails the build (pre-commit hook and CI both run `pytest -m golden`)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from recon.runs.settings import default_settings
from recon.samples.evaluate import Evaluation, evaluate
from recon.samples.generator import DEFAULT_SEED, build_sample_set
from recon.samples.loader import load_files, reconcile_all

pytestmark = pytest.mark.golden

_MANIFEST = Path(__file__).resolve().parents[2] / "data" / "golden" / "manifest.json"


@pytest.fixture()
def evaluated(db_session: Session) -> tuple[dict[str, Any], Evaluation]:
    sample = build_sample_set(DEFAULT_SEED, 1)
    settings = default_settings()
    load_files(db_session, sample, settings, sample.as_of, "system:golden")
    reconcile_all(db_session, settings, sample.as_of, sorted(settings.banks), "system:golden")
    return sample.manifest(), evaluate(db_session, sample.manifest())


def test_the_generator_still_produces_the_committed_manifest() -> None:
    fresh = build_sample_set(DEFAULT_SEED, 1).manifest()
    committed = json.loads(_MANIFEST.read_text())
    assert fresh == committed, "regenerate with `python -m recon.cli.golden --write-manifest`"


def test_the_engine_does_exactly_what_the_manifest_says(
    evaluated: tuple[dict[str, Any], Evaluation],
) -> None:
    _, report = evaluated
    assert report.passed, "\n".join(str(d) for d in report.deviations)


def test_the_gate_reports_a_deviation_when_the_engine_and_manifest_disagree(
    evaluated: tuple[dict[str, Any], Evaluation], db_session: Session
) -> None:
    """A gate that cannot fail proves nothing: corrupt two expectations and check that
    exactly those two scenarios are named."""
    manifest, _ = evaluated
    tampered = json.loads(json.dumps(manifest))
    exact = next(s for s in tampered["scenarios"] if s["kind"] == "exact")
    split = next(s for s in tampered["scenarios"] if s["kind"] == "split")
    exact["expect_match"] = "SPLIT"
    split["expect_exceptions"] = []

    report = evaluate(db_session, tampered)

    assert not report.passed
    assert {d.scenario_id for d in report.deviations} == {exact["id"], split["id"]}
    assert report.accuracy() < 1.0


def test_the_summary_by_kind_accounts_for_every_scenario(
    evaluated: tuple[dict[str, Any], Evaluation],
) -> None:
    manifest, report = evaluated
    assert sum(v["total"] for v in report.by_kind.values()) == len(manifest["scenarios"])
    assert all(v["ok"] == v["total"] for v in report.by_kind.values())
    assert report.exception_counts["FEE_DEDUCTION"] >= 1


def test_the_same_planted_cases_hold_on_a_different_calendar_week(db_session: Session) -> None:
    sample = build_sample_set(DEFAULT_SEED, 1, anchor=date(2026, 6, 4))  # a Thursday in June
    settings = default_settings()
    load_files(db_session, sample, settings, sample.as_of, "system:golden")
    reconcile_all(db_session, settings, sample.as_of, sorted(settings.banks), "system:golden")
    report = evaluate(db_session, sample.manifest())
    assert report.passed, "\n".join(str(d) for d in report.deviations)


def test_an_anchor_that_is_not_a_thursday_is_refused() -> None:
    with pytest.raises(ValueError, match="Thursday"):
        build_sample_set(DEFAULT_SEED, 1, anchor=date(2026, 3, 13))
