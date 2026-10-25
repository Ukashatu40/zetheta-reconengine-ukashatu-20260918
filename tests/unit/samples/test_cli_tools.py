"""The golden, seed and benchmark command-line tools."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from recon.bench.pipeline_bench import TARGETS_PER_MINUTE, Throughput, parse_throughput
from recon.cli import benchmark, golden, seed
from recon.samples.generator import DEFAULT_SEED, build_sample_set


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 10, 21), date(2026, 10, 15)),  # a Wednesday: the Thursday before
        (date(2026, 10, 15), date(2026, 10, 8)),  # a Thursday: strictly before today
        (date(2026, 10, 16), date(2026, 10, 15)),
    ],
)
def test_the_seed_anchors_on_the_last_thursday_before_today(today: date, expected: date) -> None:
    assert seed.last_thursday(today) == expected
    assert seed.last_thursday(today).weekday() == 3


def test_seed_refuses_an_anchor_that_is_not_a_thursday(capsys: pytest.CaptureFixture[str]) -> None:
    assert seed.main(["--anchor", "2026-03-13"]) == 2
    assert "Thursday" in capsys.readouterr().err


def test_golden_cli_writes_files_and_a_manifest_that_matches_the_generator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "manifest.json"
    monkeypatch.setattr(golden, "manifest_path", lambda: target)
    assert golden.main(["--write-manifest", "--write-files", str(tmp_path / "files")]) == 0
    assert json.loads(target.read_text()) == build_sample_set(DEFAULT_SEED, 1).manifest()
    assert len(list((tmp_path / "files").iterdir())) == 18  # 17 statements + the rate file


def test_golden_cli_with_nothing_to_do_is_a_usage_error() -> None:
    assert golden.main([]) == 2


def test_benchmark_refuses_a_database_that_is_not_a_test_database(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert benchmark.main(["--database-url", "postgresql+psycopg://u:p@localhost/recon"]) == 2
    assert "_test" in capsys.readouterr().err


def test_benchmark_needs_a_database_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    assert benchmark.main([]) == 2
    assert "TEST_DATABASE_URL" in capsys.readouterr().err


def test_throughput_is_records_per_minute() -> None:
    assert Throughput(records=1000, seconds=30.0).per_minute == pytest.approx(2000)
    assert Throughput(records=5, seconds=0.0).per_minute > 0  # never divides by zero


def test_parse_throughput_covers_every_format() -> None:
    result = parse_throughput(build_sample_set(DEFAULT_SEED, 1), repeat=1)
    assert set(result) == {"CSV", "MT940", "CAMT053"}
    assert all(t.records > 0 for t in result.values())


def test_the_targets_are_the_a6_1_figures() -> None:
    assert TARGETS_PER_MINUTE == {
        "CSV": 150_000,
        "MT940": 50_000,
        "CAMT053": 30_000,
        "ALL": 100_000,
    }
