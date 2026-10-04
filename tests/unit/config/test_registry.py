# tests/unit/config/test_registry.py
from __future__ import annotations

from recon.config.registry import load_bank_configs


def test_every_committed_bank_config_loads_keyed_by_bank_code() -> None:
    configs = load_bank_configs()
    assert set(configs) == {"HDFC", "ICICI", "AXIS", "SBI", "KOTAK"}
    assert all(c.bank_code == code for code, c in configs.items())
