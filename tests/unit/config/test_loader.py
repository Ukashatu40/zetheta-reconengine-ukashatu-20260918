# tests/unit/config/test_loader.py
"""Tests for recon.config.loader.

Includes a regression guard that loads every committed bank config
(config/banks/*.yaml) — this catches a config file breaking as a side
effect of editing the JSON Schema or the Pydantic model, without needing
one test per bank file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from recon.config.loader import BankConfigError, load_bank_config

_BANKS_DIR = Path(__file__).resolve().parents[3] / "config" / "banks"


def _write_yaml(path: Path, data: dict[str, object]) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _valid_config_dict() -> dict[str, Any]:
    return {
        "bank_code": "TEST",
        "bank_name": "Test Bank",
        "config_version": "test.v1",
        "supported_formats": ["CSV"],
        "timezone": "Asia/Kolkata",
        "currency_default": "INR",
        "settlement_cycle": "T+1",
        "reconciliation_window_days": 1,
        "csv": {
            "date_format": "%d-%m-%Y",
            "column_mapping": {
                "reference": "Ref",
                "txn_date": "Date",
                "amount": "Amount",
                "direction": "Type",
            },
        },
    }


@pytest.mark.parametrize("config_path", sorted(_BANKS_DIR.glob("*.yaml")), ids=lambda p: p.name)
def test_every_committed_bank_config_loads_successfully(config_path: Path) -> None:
    config = load_bank_config(config_path)
    assert config.bank_code
    assert config.csv is not None


def test_valid_config_loads_with_expected_fields(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path / "test.v1.yaml", _valid_config_dict())
    config = load_bank_config(path)
    assert config.bank_code == "TEST"
    assert config.currency_default == "INR"
    assert config.csv is not None
    assert config.csv.column_mapping["reference"] == "Ref"


def test_missing_required_top_level_field_fails_schema_validation(tmp_path: Path) -> None:
    data = _valid_config_dict()
    del data["timezone"]
    path = _write_yaml(tmp_path / "bad.yaml", data)
    with pytest.raises(BankConfigError, match="schema validation failed"):
        load_bank_config(path)


def test_missing_required_csv_column_mapping_fails_schema_validation(tmp_path: Path) -> None:
    data = _valid_config_dict()
    del data["csv"]["column_mapping"]
    path = _write_yaml(tmp_path / "bad.yaml", data)
    with pytest.raises(BankConfigError, match="schema validation failed"):
        load_bank_config(path)


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    data = _valid_config_dict()
    data["unexpected_field"] = "should not be here"
    path = _write_yaml(tmp_path / "bad.yaml", data)
    with pytest.raises(BankConfigError, match="schema validation failed"):
        load_bank_config(path)


def test_invalid_timezone_fails_model_validation(tmp_path: Path) -> None:
    data = _valid_config_dict()
    data["timezone"] = "Not/A_Real_Zone"
    path = _write_yaml(tmp_path / "bad.yaml", data)
    with pytest.raises(BankConfigError, match="not a known IANA timezone"):
        load_bank_config(path)


def test_column_mapping_missing_core_field_fails_model_validation(tmp_path: Path) -> None:
    data = _valid_config_dict()
    del data["csv"]["column_mapping"]["direction"]
    path = _write_yaml(tmp_path / "bad.yaml", data)
    with pytest.raises(BankConfigError, match="missing required canonical fields"):
        load_bank_config(path)


def test_currency_default_is_uppercased(tmp_path: Path) -> None:
    data = _valid_config_dict()
    data["currency_default"] = "inr"
    path = _write_yaml(tmp_path / "test.v1.yaml", data)
    config = load_bank_config(path)
    assert config.currency_default == "INR"
