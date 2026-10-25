# src/recon/matching/stage_config.py
"""Per-bank configuration of the rule-based matching stages (A3.3).

Builds the ordered rule registry (Amount Tolerance, then Date Offset) from a
bank's configuration, plus the settings for the cross-currency and group
(split / netted) strategies. The registry is rebuilt per run, so a hot-swapped
bank config takes effect on the next run.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from recon.calendar import default_calendar
from recon.config.models import BankConfig
from recon.domain.money import known_currencies
from recon.matching.rules.amount_tolerance import AmountToleranceRule
from recon.matching.rules.base import RuleRegistry
from recon.matching.rules.date_offset import DateOffsetRule
from recon.matching.strategies.group import GroupMatchConfig

_DEFAULT_TOLERANCE_MINOR = 1
_PENNY_EXPONENT = 2  # "0.01" is one minor unit for a two-decimal currency
_RULE_MAX_CALENDAR_DAYS = 3  # A3.4: "T+3 max"


@dataclass(frozen=True, slots=True)
class RuleStageConfig:
    registry: RuleRegistry
    group: GroupMatchConfig
    fx_tolerance_percent: Decimal


def penny_tolerances() -> dict[str, int]:
    """±0.01 expressed in each currency's own minor units: 1 for INR, 10 for a
    three-decimal currency, 0 for a currency with no minor unit (JPY)."""
    return {
        currency: 10 ** (exponent - _PENNY_EXPONENT) if exponent >= _PENNY_EXPONENT else 0
        for currency, exponent in known_currencies().items()
        if exponent != _PENNY_EXPONENT
    }


def build_rule_stage(bank: BankConfig | None) -> RuleStageConfig:
    tolerance = bank.amount_tolerance_minor if bank else _DEFAULT_TOLERANCE_MINOR
    fx_tolerance = bank.fx_tolerance_percent if bank else Decimal("1.5")
    calendar = default_calendar(bank.calendar if bank else "in")
    date_offset = bank.date_offset_days if bank else 1

    registry = RuleRegistry()
    registry.register(
        AmountToleranceRule(
            default_tolerance_minor=tolerance,
            tolerance_minor_by_currency=penny_tolerances(),
            max_days_apart=_RULE_MAX_CALENDAR_DAYS,
        )
    )
    registry.register(DateOffsetRule(calendar, max_business_days=date_offset))
    return RuleStageConfig(
        registry=registry,
        group=GroupMatchConfig(
            tolerance_minor=tolerance,
            fx_tolerance_percent=fx_tolerance,
            date_window_days=max(2, date_offset),
        ),
        fx_tolerance_percent=fx_tolerance,
    )
