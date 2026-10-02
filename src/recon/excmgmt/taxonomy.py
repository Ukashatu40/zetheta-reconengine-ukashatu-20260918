# src/recon/excmgmt/taxonomy.py
"""Exception taxonomy configuration (A4.1, A4.2): per-category SLA,
base severity, auto-resolvability and default tier, loaded from
config/exceptions/taxonomy.yaml and validated so every
ExceptionCategory appears exactly once.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from recon.domain.enums import EscalationTier, ExceptionCategory, ExceptionSeverity

DEFAULT_TAXONOMY_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "exceptions" / "taxonomy.yaml"
)


class TaxonomyConfigError(ValueError):
    """Raised when the taxonomy file is malformed or incomplete."""


class AutoResolvable(StrEnum):
    ALWAYS = "ALWAYS"
    NEVER = "NEVER"
    CONDITIONAL = "CONDITIONAL"


class CategoryRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    category: ExceptionCategory
    auto_resolvable: AutoResolvable
    sla_minutes: int = Field(gt=0)
    base_severity: ExceptionSeverity
    default_tier: EscalationTier
    source: str


class ValueThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    critical_individual_inr_minor: int = Field(gt=0)
    critical_cumulative_daily_inr_minor: int = Field(gt=0)


class AutoResolveThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount_mismatch_max_difference_minor: int = Field(ge=0)
    date_mismatch_max_days: int = Field(ge=0)
    reference_truncated_min_confidence: Decimal


class EscalationThresholds(BaseModel):
    model_config = ConfigDict(frozen=True)

    fx_variance_tier4_percent: Decimal
    systemic_exception_count: int = Field(gt=0)


class TaxonomyConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    config_version: str
    value_thresholds: ValueThresholds
    auto_resolve: AutoResolveThresholds
    escalation: EscalationThresholds
    categories: list[CategoryRule]

    @model_validator(mode="after")
    def _covers_every_category_once(self) -> TaxonomyConfig:
        listed = [rule.category for rule in self.categories]
        missing = sorted(set(ExceptionCategory) - set(listed))
        duplicated = sorted({c for c in listed if listed.count(c) > 1})
        if missing or duplicated:
            raise ValueError(
                f"taxonomy must list every category exactly once; "
                f"missing: {missing}, duplicated: {duplicated}"
            )
        return self

    def rule_for(self, category: ExceptionCategory) -> CategoryRule:
        for rule in self.categories:
            if rule.category == category:
                return rule
        raise LookupError(f"no rule for {category}")  # unreachable after validation


def load_taxonomy(path: Path = DEFAULT_TAXONOMY_PATH) -> TaxonomyConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TaxonomyConfigError(f"{path}: expected a YAML mapping at the top level")
    try:
        return TaxonomyConfig.model_validate(data)
    except PydanticValidationError as exc:
        raise TaxonomyConfigError(f"{path}: {exc}") from exc
