# tests/unit/config/test_matching_loader.py
"""Tests for recon.config.matching_loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from recon.config.matching_loader import MatchingConfigError, load_matching_config

_MATCHING_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "matching" / "weights.yaml"


def test_committed_weights_yaml_loads_and_matches_a34_exactly() -> None:
    config = load_matching_config(_MATCHING_CONFIG_PATH)

    assert config.weights.reference == 0.35
    assert config.weights.amount == 0.25
    assert config.weights.date == 0.15
    assert config.weights.counterparty == 0.10
    assert config.weights.direction == 0.10
    assert config.weights.currency == 0.05
    assert config.thresholds.auto_match_confidence == 0.85
    assert config.thresholds.review_confidence == 0.60


def test_weights_not_summing_to_one_is_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load(_MATCHING_CONFIG_PATH.read_text())
    data["weights"]["reference"] = 0.99  # now sums to well over 1.0
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data))

    with pytest.raises(MatchingConfigError, match=r"sum to 1\.0"):
        load_matching_config(path)


def test_review_threshold_not_below_auto_match_is_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load(_MATCHING_CONFIG_PATH.read_text())
    data["thresholds"]["review_confidence"] = 0.90  # above auto_match_confidence
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data))

    with pytest.raises(MatchingConfigError, match="strictly less than"):
        load_matching_config(path)


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    data = yaml.safe_load(_MATCHING_CONFIG_PATH.read_text())
    data["unexpected"] = "nope"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data))

    with pytest.raises(MatchingConfigError, match="schema validation failed"):
        load_matching_config(path)
