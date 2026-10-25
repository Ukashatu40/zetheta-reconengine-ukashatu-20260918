# src/recon/runs/settings.py
"""Settings a run needs. The blocking window and stale window come from each
bank's reconciliation_window_days; the amount tolerance comes from the bank's
amount_tolerance_minor (default 1, i.e. ±0.01 as A3.3 states)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import lru_cache

from recon.anomaly.config import AnomalyConfig, load_anomaly_config
from recon.config.matching_loader import load_matching_config
from recon.config.matching_models import MatchingConfig
from recon.config.models import BankConfig
from recon.config.registry import LiveBankConfigs
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
    anomaly: AnomalyConfig
    set_based_exact: bool = True

    def stale_after_days(self, bank_code: str) -> int | None:
        config = self.banks.get(bank_code)
        return config.reconciliation_window_days if config else None

    def amount_tolerance_for(self, bank_code: str) -> int:
        """±0.01 per A3.3/A3.4 unless the bank's config sets its own tolerance."""
        config = self.banks.get(bank_code)
        return config.amount_tolerance_minor if config else self.amount_tolerance_minor

    def blocking_for(self, bank_code: str) -> BlockingConfig:
        window = self.stale_after_days(bank_code)
        if window is None:
            return self.blocking
        return replace(self.blocking, date_window_days=max(1, window))


@lru_cache(maxsize=1)
def default_settings() -> RunSettings:
    return RunSettings(
        blocking=BlockingConfig(amount_bucket_width_minor=10_000, date_window_days=2),
        amount_tolerance_minor=1,
        matching=load_matching_config(config_dir() / "matching" / "weights.yaml"),
        taxonomy=load_taxonomy(),
        stale_run_after=timedelta(minutes=30),
        banks=LiveBankConfigs(),
        anomaly=load_anomaly_config(),
    )
