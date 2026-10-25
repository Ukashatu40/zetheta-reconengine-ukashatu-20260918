# src/recon/anomaly/config.py
"""Thresholds for the anomaly rules and the rate alerts, loaded from
config/anomaly/rules.yaml. EXCEPTION_RATE_ALERT_THRESHOLD and
SUSPICIOUS_MATCH_RATE_THRESHOLD (see .env.example) override the two rate
thresholds so an operator can tune them without editing the file."""

from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from recon.paths import config_dir


class AnomalyConfigError(ValueError):
    """The anomaly configuration is malformed."""


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True)


class AmountDeviationConfig(_Section):
    sigma: Decimal = Field(gt=0)
    min_history: int = Field(gt=1)
    whale_multiplier: Decimal = Field(gt=1)


class VelocityConfig(_Section):
    multiplier: Decimal = Field(gt=0)
    window_minutes: int = Field(gt=0)
    min_count: int = Field(gt=0)
    min_active_days: int = Field(gt=0)


class AbsenceConfig(_Section):
    grace_minutes: int = Field(ge=0)


class DataVolumeConfig(_Section):
    multiplier: Decimal = Field(gt=1)
    min_files: int = Field(gt=1)


class SettlementUniquenessConfig(_Section):
    overlap_fraction: Decimal = Field(gt=0, le=1)
    min_overlap_count: int = Field(gt=0)


class RateConfig(_Section):
    threshold: Decimal = Field(gt=0, le=1)


class MatchRateConfig(RateConfig):
    min_externals: int = Field(ge=0)


class ExceptionRateConfig(RateConfig):
    min_transactions: int = Field(ge=0)


class BacklogConfig(_Section):
    open_exceptions: int = Field(gt=0)


class AnomalyConfig(_Section):
    config_version: str
    amount_deviation: AmountDeviationConfig
    velocity: VelocityConfig
    absence: AbsenceConfig
    data_volume: DataVolumeConfig
    settlement_uniqueness: SettlementUniquenessConfig
    match_rate: MatchRateConfig
    exception_rate: ExceptionRateConfig
    backlog: BacklogConfig


def load_anomaly_config(path: Path | None = None) -> AnomalyConfig:
    path = path if path is not None else config_dir() / "anomaly" / "rules.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AnomalyConfigError(f"{path}: expected a YAML mapping at the top level")
    try:
        config = AnomalyConfig.model_validate(data)
    except PydanticValidationError as exc:
        raise AnomalyConfigError(f"{path}: {exc}") from exc
    overrides: dict[str, Decimal] = {}
    for name, env in (
        ("match_rate", "SUSPICIOUS_MATCH_RATE_THRESHOLD"),
        ("exception_rate", "EXCEPTION_RATE_ALERT_THRESHOLD"),
    ):
        raw = os.environ.get(env)
        if raw:
            try:
                overrides[name] = Decimal(raw)
            except ArithmeticError as exc:
                raise AnomalyConfigError(f"{env}={raw!r} is not a number") from exc
    if not overrides:
        return config
    return config.model_copy(
        update={
            name: getattr(config, name).model_copy(update={"threshold": value})
            for name, value in overrides.items()
        }
    )


@lru_cache(maxsize=1)
def default_anomaly_config() -> AnomalyConfig:
    return load_anomaly_config()
