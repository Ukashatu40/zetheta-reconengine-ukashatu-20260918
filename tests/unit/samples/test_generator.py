"""The sample generator: determinism, the A8.3 minimums, and that files are what they claim."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from recon.config.registry import load_bank_configs
from recon.domain.enums import ExceptionCategory
from recon.fx.loader import parse_fx_csv
from recon.ingestion.pan import mask_pans
from recon.ingestion.parsers.camt053.parser import CAMT053Parser
from recon.ingestion.parsers.csv_parser import CSVParser
from recon.ingestion.parsers.mt940.state_machine import MT940Parser
from recon.samples.generator import DEFAULT_SEED, build_sample_set

_SET = build_sample_set(DEFAULT_SEED, 1)


def test_the_same_seed_produces_byte_identical_files_and_manifest() -> None:
    again = build_sample_set(DEFAULT_SEED, 1)
    assert [f.sha256 for f in again.files] == [f.sha256 for f in _SET.files]
    assert again.manifest() == _SET.manifest()
    assert again.fx_rates_csv == _SET.fx_rates_csv


def test_a_different_seed_changes_the_data_but_not_the_planted_cases() -> None:
    other = build_sample_set(DEFAULT_SEED + 1, 1)
    assert [f.sha256 for f in other.files] != [f.sha256 for f in _SET.files]
    assert other.manifest()["scenario_counts"] == _SET.manifest()["scenario_counts"]
    assert (
        other.manifest()["exception_categories_planted"]
        == _SET.manifest()["exception_categories_planted"]
    )


def test_scale_multiplies_volume_but_not_the_planted_exceptions() -> None:
    big = build_sample_set(DEFAULT_SEED, 10).manifest()["scenario_counts"]
    small = _SET.manifest()["scenario_counts"]
    assert big["exact"] == 10 * small["exact"] and big["fuzzy_auto"] == 10 * small["fuzzy_auto"]
    assert big["category"] == small["category"] and big["split"] == small["split"] == 5


def test_a_scale_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="scale"):
        build_sample_set(DEFAULT_SEED, 0)


def test_the_dataset_meets_the_a8_3_minimums() -> None:
    counts = _SET.manifest()["scenario_counts"]
    assert counts["exact"] >= 100
    assert counts["fuzzy_auto"] + counts["fuzzy_review"] + counts["tolerance"] >= 50
    assert counts["split"] >= 5 and counts["netted"] >= 5


def test_every_one_of_the_19_exception_categories_is_planted() -> None:
    planted = set(_SET.manifest()["exception_categories_planted"])
    assert planted == {c.value for c in ExceptionCategory}


def test_every_scenario_names_the_records_it_is_about() -> None:
    for s in _SET.scenarios:
        assert s.internal_refs or s.external_refs, s.id
    ids = [s.id for s in _SET.scenarios]
    assert len(ids) == len(set(ids))


def test_no_two_files_share_content_so_duplicate_detection_never_rejects_a_sample() -> None:
    hashes = [f.sha256 for f in _SET.files]
    assert len(hashes) == len(set(hashes))


def test_the_manifest_is_valid_json_with_the_files_and_hashes() -> None:
    manifest = json.loads(json.dumps(_SET.manifest()))
    assert {f["format"] for f in manifest["files"]} == {"CSV", "MT940", "CAMT053"}
    assert all(len(f["sha256"]) == 64 for f in manifest["files"])


def test_the_files_contain_no_card_numbers() -> None:
    for f in _SET.files:
        assert mask_pans(f.content).masked_count == 0, f.name


@pytest.mark.parametrize("fmt", ["CSV", "MT940", "CAMT053"])
def test_every_file_is_read_back_by_its_real_parser(fmt: str) -> None:
    banks = load_bank_configs()
    seen = 0
    for f in _SET.files:
        if f.format_type != fmt:
            continue
        config = banks[f.bank]
        if fmt == "CSV":
            rows = list(CSVParser().parse(io.StringIO(f.content), config))
        elif fmt == "MT940":
            rows = list(MT940Parser().parse(io.StringIO(f.content), config))
        else:
            rows = list(CAMT053Parser().parse(io.BytesIO(f.content.encode()), config))
        assert rows, f.name
        invalid = [r for r in rows if not r.is_valid]
        # only the one planted corrupt HDFC row may fail to parse
        assert len(invalid) <= 1 and (not invalid or f.name.startswith("hdfc_settlement"))
        seen += 1
    assert seen > 0


def test_write_to_creates_the_files_and_the_rate_file(tmp_path: Path) -> None:
    _SET.write_to(tmp_path / "out")
    names = {p.name for p in (tmp_path / "out").iterdir()}
    assert {f.name for f in _SET.files} <= names
    assert "fx_rates_20260302_20260313.csv" in names


def test_the_rate_file_has_three_planted_wrong_rates_the_loader_flags() -> None:
    result = parse_fx_csv(io.StringIO(_SET.fx_rates_csv))
    assert len(result.rates) == 72 and len(result.suspects) == 3
