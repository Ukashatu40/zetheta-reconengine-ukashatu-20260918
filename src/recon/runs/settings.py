# src/recon/runs/settings.py
"""Settings a run needs. Defaults only for now: not yet per-bank (DD-16)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from recon.config.matching_loader import load_matching_config
from recon.config.matching_models import MatchingConfig
from recon.excmgmt.taxonomy import TaxonomyConfig, load_taxonomy
from recon.matching.blocking.candidates import BlockingConfig

_WEIGHTS_PATH = Path(__file__).resolve().parents[3] / "config" / "matching" / "weights.yaml"


@dataclass(frozen=True, slots=True)
class RunSettings:
    blocking: BlockingConfig
    amount_tolerance_minor: int
    matching: MatchingConfig
    taxonomy: TaxonomyConfig
    stale_run_after: timedelta


@lru_cache(maxsize=1)
def default_settings() -> RunSettings:
    return RunSettings(
        blocking=BlockingConfig(amount_bucket_width_minor=10_000, date_window_days=2),
        amount_tolerance_minor=100,
        matching=load_matching_config(_WEIGHTS_PATH),
        taxonomy=load_taxonomy(),
        stale_run_after=timedelta(minutes=30),
    )
