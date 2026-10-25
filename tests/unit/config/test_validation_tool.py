"""The column-mapping validation tool and its CLI (B4.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from recon.cli.validate_bank_config import main, render
from recon.config.validation import validate_candidate
from recon.paths import config_dir

_ICICI_YAML = (config_dir() / "banks" / "icici.v1.yaml").read_text()
_HEADER = "Txn Ref No,Txn Date,Txn Amount,Type,Party Name,Remarks"
_ROW = "UTR1,2026-03-15,1500.00,CR,Acme,payment"


def test_a_valid_configuration_without_a_sample_passes_with_a_warning() -> None:
    report = validate_candidate(_ICICI_YAML, "icici.yaml")
    assert report.ok is True and report.bank_code == "ICICI"
    assert "no sample file" in report.warnings[0]


def test_an_invalid_configuration_is_reported_not_raised() -> None:
    report = validate_candidate("bank_code: X\n", "bad.yaml")
    assert report.ok is False
    assert "schema validation failed" in report.config_errors[0]


def test_unparseable_yaml_is_reported() -> None:
    report = validate_candidate("a: [unclosed", "bad.yaml")
    assert report.ok is False and "not valid YAML" in report.config_errors[0]


def test_a_matching_sample_passes_and_previews_the_mapped_fields() -> None:
    report = validate_candidate(_ICICI_YAML, "icici.yaml", f"{_HEADER}\n{_ROW}\n")
    assert report.ok is True
    assert report.sample is not None
    assert (report.sample.rows_checked, report.sample.rows_valid) == (1, 1)
    assert report.sample.preview[0]["reference"] == "UTR1"


def test_b41_the_changed_layout_is_flagged_before_anything_is_deployed() -> None:
    changed = (
        "Txn Ref No,Tax Deducted,Net Amount,Txn Amount,Type,Party Name,Remarks\n"
        "UTR1,5,95,100.00,CR,Acme,x\n"
    )
    report = validate_candidate(_ICICI_YAML, "icici.yaml", changed)
    # the date column was dropped from this sample, so the mapping cannot be satisfied
    assert report.ok is False and report.sample is not None
    assert report.sample.change.missing == ["Txn Date"]
    assert report.sample.change.unexpected == ["Tax Deducted", "Net Amount"]
    assert report.sample.rows_checked == 0


def test_new_columns_alone_are_a_warning_not_a_failure() -> None:
    sample = f"{_HEADER},Tax Deducted\n{_ROW},5\n"
    report = validate_candidate(_ICICI_YAML, "icici.yaml", sample)
    assert report.ok is True
    assert any("Tax Deducted" in w for w in report.warnings)


def test_a_sample_with_a_malformed_row_fails_and_lists_the_problem() -> None:
    sample = f"{_HEADER}\n{_ROW}\nUTR2,not-a-date,1.00,CR,Acme,x\n"
    report = validate_candidate(_ICICI_YAML, "icici.yaml", sample)
    assert report.ok is False and report.sample is not None
    assert (report.sample.rows_checked, report.sample.rows_valid) == (2, 1)
    assert any("line" in e and "INVALID_DATE" in e for e in report.sample.errors)


def test_an_empty_sample_fails() -> None:
    assert validate_candidate(_ICICI_YAML, "icici.yaml", f"{_HEADER}\n").ok is False


def test_the_sample_check_stops_after_the_row_limit() -> None:
    rows = "\n".join(_ROW for _ in range(100))
    report = validate_candidate(_ICICI_YAML, "icici.yaml", f"{_HEADER}\n{rows}\n")
    assert report.sample is not None and report.sample.rows_checked == 25


def test_a_config_without_a_csv_section_cannot_check_a_sample() -> None:
    mt940_only = (
        "bank_code: MTB\nbank_name: MT Bank\nconfig_version: mtb.v1\n"
        "supported_formats: [MT940]\ntimezone: Asia/Kolkata\ncurrency_default: INR\n"
        'settlement_cycle: "T+1"\nreconciliation_window_days: 1\n'
        'mt940:\n  decimal_separator: ","\n'
    )
    report = validate_candidate(mt940_only, "mtb.yaml", "anything\n")
    assert report.ok is True and "no CSV section" in report.warnings[0]


def test_the_cli_exits_zero_on_a_pass_and_one_on_a_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "icici.yaml"
    config.write_text(_ICICI_YAML)
    good = tmp_path / "good.csv"
    good.write_text(f"{_HEADER}\n{_ROW}\n")
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")

    assert main([str(config), "--sample", str(good)]) == 0
    assert "RESULT: PASS" in capsys.readouterr().out
    assert main([str(config), "--sample", str(bad)]) == 1
    output = capsys.readouterr().out
    assert "RESULT: FAIL" in output and "missing columns" in output


def test_the_cli_reports_unreadable_input_as_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(tmp_path / "nope.yaml")]) == 2
    assert "cannot read input" in capsys.readouterr().err


def test_render_lists_config_errors() -> None:
    assert "INVALID" in render(validate_candidate("x: 1\n", "bad.yaml"))
