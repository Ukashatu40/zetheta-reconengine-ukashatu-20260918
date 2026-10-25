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
    if "CSV" in config.supported_formats:
        assert config.csv is not None
    if "MT940" in config.supported_formats:
        assert config.mt940 is not None
    # CAMT053 has no bank-specific config section — nothing to assert here.


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


# -- matching behaviour fields (A3.3) -----------------------------------------


def test_matching_fields_default_when_absent(tmp_path: Path) -> None:
    config = load_bank_config(_write_yaml(tmp_path / "t.yaml", _valid_config_dict()))
    assert (config.calendar, config.amount_tolerance_minor) == ("in", 1)
    assert (str(config.fx_tolerance_percent), config.fee_schedule) == ("1.5", None)
    assert (config.settlement_days, config.date_offset_days) == (1, 1)


def test_matching_fields_can_be_configured(tmp_path: Path) -> None:
    data = _valid_config_dict() | {
        "settlement_cycle": "T+3",
        "date_offset_business_days": 2,
        "amount_tolerance_minor": 5,
        "fx_tolerance_percent": "0.5",
        "expected_settlement_time": "18:30",
        "fee_schedule": {"fixed_minor": 500, "percent": "0.1", "tolerance_minor": 2},
    }
    config = load_bank_config(_write_yaml(tmp_path / "t.yaml", data))
    assert (config.settlement_days, config.date_offset_days) == (3, 2)
    assert str(config.fx_tolerance_percent) == "0.5"
    assert config.fee_schedule is not None
    assert (config.fee_schedule.fixed_minor, str(config.fee_schedule.percent)) == (500, "0.1")
    assert config.expected_settlement_time == "18:30"


def test_the_date_offset_follows_the_settlement_cycle_when_not_set(tmp_path: Path) -> None:
    data = _valid_config_dict() | {"settlement_cycle": "T+2"}
    assert load_bank_config(_write_yaml(tmp_path / "t.yaml", data)).date_offset_days == 2


def test_an_unparseable_settlement_cycle_counts_as_t_plus_1(tmp_path: Path) -> None:
    data = _valid_config_dict() | {"settlement_cycle": "NEXT DAY"}
    config = load_bank_config(_write_yaml(tmp_path / "t.yaml", data))
    assert (config.settlement_days, config.date_offset_days) == (1, 1)


@pytest.mark.parametrize("bad_time", ["6pm", "24:00", "18:60", "1830"])
def test_a_malformed_expected_settlement_time_is_rejected(tmp_path: Path, bad_time: str) -> None:
    data = _valid_config_dict() | {"expected_settlement_time": bad_time}
    with pytest.raises(BankConfigError):
        load_bank_config(_write_yaml(tmp_path / "t.yaml", data))


def test_an_unknown_matching_field_is_still_rejected(tmp_path: Path) -> None:
    data = _valid_config_dict() | {"fee_schedule": {"fixed": 5}}
    with pytest.raises(BankConfigError, match="schema validation"):
        load_bank_config(_write_yaml(tmp_path / "t.yaml", data))
