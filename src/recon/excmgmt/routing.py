# src/recon/excmgmt/routing.py
"""Pure routing decisions for an exception: severity (AE-07, independent
of SLA), initial escalation tier (A4.2), SLA deadline, and tier after an
SLA breach. No I/O.

Tier order of precedence:
  1. Tier 4: category forced to compliance (DIRECTION_REVERSAL per AE-03,
     REGULATORY_HOLD), FX variance strictly beyond the threshold, or a
     systemic failure (strictly more than N exceptions for one bank batch).
  2. Tier 3: individual value or cumulative daily value strictly above the
     configured thresholds. This overrides auto-resolution: a large
     exception is never auto-resolved.
  3. Tier 1: rule-eligible for auto-resolution.
  4. Otherwise the category's default tier.

A conditional category with missing context is never auto-resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from recon.domain.enums import EscalationTier, ExceptionCategory, ExceptionSeverity
from recon.excmgmt.taxonomy import AutoResolvable, CategoryRule, TaxonomyConfig

_SEVERITY_ORDER = {
    ExceptionSeverity.LOW: 0,
    ExceptionSeverity.MEDIUM: 1,
    ExceptionSeverity.HIGH: 2,
    ExceptionSeverity.CRITICAL: 3,
}
_MAX_TIER = EscalationTier.TIER_4_COMPLIANCE


@dataclass(frozen=True, slots=True)
class ExceptionContext:
    category: ExceptionCategory
    amount_inr_minor: int | None = None
    cumulative_daily_inr_minor: int | None = None
    amount_difference_minor: int | None = None
    days_offset: int | None = None
    fuzzy_confidence: Decimal | None = None
    fx_variance_percent: Decimal | None = None
    bank_batch_exception_count: int = 0


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    severity: ExceptionSeverity
    tier: EscalationTier
    auto_resolve_eligible: bool
    sla_minutes: int
    sla_deadline: datetime
    rationale: str


def route(ctx: ExceptionContext, taxonomy: TaxonomyConfig, created_at: datetime) -> RoutingDecision:
    if created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")
    rule = taxonomy.rule_for(ctx.category)
    eligible = _auto_resolve_eligible(ctx, rule, taxonomy)
    tier, reason = _tier(ctx, rule, taxonomy, eligible)
    return RoutingDecision(
        severity=_severity(ctx, rule, taxonomy),
        tier=tier,
        auto_resolve_eligible=eligible,
        sla_minutes=rule.sla_minutes,
        sla_deadline=created_at + timedelta(minutes=rule.sla_minutes),
        rationale=reason,
    )


def tier_after_sla_check(
    current: EscalationTier, sla_deadline: datetime, now: datetime
) -> EscalationTier:
    """One tier up (capped at 4) once now is strictly past the deadline."""
    if now <= sla_deadline:
        return current
    return EscalationTier(min(current.value + 1, _MAX_TIER.value))


def _auto_resolve_eligible(ctx: ExceptionContext, rule: CategoryRule, tax: TaxonomyConfig) -> bool:
    if rule.auto_resolvable is AutoResolvable.ALWAYS:
        return True
    if rule.auto_resolvable is AutoResolvable.NEVER:
        return False
    limits = tax.auto_resolve
    if ctx.category is ExceptionCategory.AMOUNT_MISMATCH:
        diff = ctx.amount_difference_minor
        return diff is not None and diff <= limits.amount_mismatch_max_difference_minor
    if ctx.category is ExceptionCategory.DATE_MISMATCH:
        days = ctx.days_offset
        return days is not None and days <= limits.date_mismatch_max_days
    if ctx.category is ExceptionCategory.REFERENCE_TRUNCATED:
        conf = ctx.fuzzy_confidence
        return conf is not None and conf > limits.reference_truncated_min_confidence
    return False


def _severity(ctx: ExceptionContext, rule: CategoryRule, tax: TaxonomyConfig) -> ExceptionSeverity:
    """AE-07: max(category base, value-derived), independent of SLA."""
    severity = rule.base_severity
    amount = ctx.amount_inr_minor
    if (amount is not None and amount > tax.value_thresholds.critical_individual_inr_minor) and (
        _SEVERITY_ORDER[ExceptionSeverity.CRITICAL] > _SEVERITY_ORDER[severity]
    ):
        severity = ExceptionSeverity.CRITICAL
    return severity


def _tier4_reason(ctx: ExceptionContext, rule: CategoryRule, tax: TaxonomyConfig) -> str | None:
    if rule.default_tier is _MAX_TIER:
        return f"{ctx.category} is routed to compliance"
    fx = ctx.fx_variance_percent
    if (
        ctx.category is ExceptionCategory.FX_VARIANCE
        and fx is not None
        and fx > tax.escalation.fx_variance_tier4_percent
    ):
        return f"FX variance {fx}% exceeds {tax.escalation.fx_variance_tier4_percent}%"
    if ctx.bank_batch_exception_count > tax.escalation.systemic_exception_count:
        return (
            f"{ctx.bank_batch_exception_count} exceptions from one bank batch "
            f"(systemic failure threshold {tax.escalation.systemic_exception_count})"
        )
    return None


def _exceeds_value(ctx: ExceptionContext, tax: TaxonomyConfig) -> bool:
    limits = tax.value_thresholds
    individual = ctx.amount_inr_minor
    cumulative = ctx.cumulative_daily_inr_minor
    return (individual is not None and individual > limits.critical_individual_inr_minor) or (
        cumulative is not None and cumulative > limits.critical_cumulative_daily_inr_minor
    )


def _tier(
    ctx: ExceptionContext, rule: CategoryRule, tax: TaxonomyConfig, eligible: bool
) -> tuple[EscalationTier, str]:
    tier4 = _tier4_reason(ctx, rule, tax)
    if tier4 is not None:
        return EscalationTier.TIER_4_COMPLIANCE, tier4
    if _exceeds_value(ctx, tax):
        return EscalationTier.TIER_3_SENIOR, "value above the senior-operations threshold"
    if eligible:
        return EscalationTier.TIER_1_AUTO, "eligible for auto-resolution"
    return rule.default_tier, "category default tier"
