# src/recon/runs/settings.py
"""Settings a run needs. The blocking window and stale window come from each
bank's reconciliation_window_days; the amount tolerance is still a default
because BankConfig has no such field (DD-16)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import lru_cache

from recon.config.matching_loader import load_matching_config
from recon.config.matching_models import MatchingConfig
from recon.config.models import BankConfig
from recon.config.registry import default_bank_configs
from recon.excmgmt.taxonomy import TaxonomyConfig, load_taxonomy
from recon.matching.blocking.candidates import BlockingConfig
from recon.paths import config_dir


@dataclass(frozen=True, slots=True)
class RunSettings:
    blocking: BlockingConfig
    amount_tolerance_minor: int
    matching: MatchingConfig
    taxonomy: TaxonomyConfig
    stale_run_after: timedelta
    banks: Mapping[str, BankConfig]

    def stale_after_days(self, bank_code: str) -> int | None:
        config = self.banks.get(bank_code)
        return config.reconciliation_window_days if config else None

    def blocking_for(self, bank_code: str) -> BlockingConfig:
        window = self.stale_after_days(bank_code)
        if window is None:
            return self.blocking
        return replace(self.blocking, date_window_days=max(1, window))


@lru_cache(maxsize=1)
def default_settings() -> RunSettings:
    return RunSettings(
        blocking=BlockingConfig(amount_bucket_width_minor=10_000, date_window_days=2),
        amount_tolerance_minor=100,
        matching=load_matching_config(config_dir() / "matching" / "weights.yaml"),
        taxonomy=load_taxonomy(),
        stale_run_after=timedelta(minutes=30),
        banks=default_bank_configs(),
    )
