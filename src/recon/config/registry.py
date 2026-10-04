# src/recon/config/registry.py
"""Loads every bank configuration under config/banks, keyed by bank_code."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from recon.config.loader import load_bank_config
from recon.config.models import BankConfig

DEFAULT_BANKS_DIR = Path(__file__).resolve().parents[3] / "config" / "banks"


class DuplicateBankCodeError(ValueError):
    """Two config files declare the same bank_code."""


def load_bank_configs(directory: Path = DEFAULT_BANKS_DIR) -> dict[str, BankConfig]:
    configs: dict[str, BankConfig] = {}
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        config = load_bank_config(path)
        if config.bank_code in configs:
            raise DuplicateBankCodeError(f"{config.bank_code} is declared in more than one file")
        configs[config.bank_code] = config
    return configs


@lru_cache(maxsize=1)
def default_bank_configs() -> dict[str, BankConfig]:
    return load_bank_configs()
