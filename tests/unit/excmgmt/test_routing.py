# tests/unit/excmgmt/test_routing.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from recon.domain.enums import EscalationTier, ExceptionCategory, ExceptionSeverity
from recon.excmgmt.routing import ExceptionContext, RoutingDecision, route, tier_after_sla_check
from recon.excmgmt.taxonomy import load_taxonomy

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
_LAKH = 10_000_000  # Rs 1 lakh in paise
_T = load_taxonomy()


def _route(category: ExceptionCategory, **kw: object) -> RoutingDecision:
    return route(ExceptionContext(category=category, **kw), _T, _NOW)  # type: ignore[arg-type]


def test_duplicate_internal_small_value_is_tier_1() -> None:
    d = _route(ExceptionCategory.DUPLICATE_INTERNAL, amount_inr_minor=5_000)
    assert d.tier is EscalationTier.TIER_1_AUTO
    assert d.auto_resolve_eligible is True


def test_high_value_overrides_auto_resolution() -> None:
    d = _route(ExceptionCategory.DUPLICATE_INTERNAL, amount_inr_minor=11 * 10 * _LAKH)
    assert d.tier is EscalationTier.TIER_3_SENIOR
    assert d.severity is ExceptionSeverity.CRITICAL


def test_value_exactly_at_threshold_is_not_high_value() -> None:
    d = _route(ExceptionCategory.MISSING_INTERNAL, amount_inr_minor=100_000_000)
    assert d.tier is EscalationTier.TIER_2_ANALYST


def test_cumulative_daily_value_above_one_crore_is_tier_3() -> None:
    d = _route(
        ExceptionCategory.MISSING_EXTERNAL,
        amount_inr_minor=1_000,
        cumulative_daily_inr_minor=1_000_000_001,
    )
    assert d.tier is EscalationTier.TIER_3_SENIOR


@pytest.mark.parametrize(
    ("diff", "tier"), [(1, EscalationTier.TIER_1_AUTO), (2, EscalationTier.TIER_2_ANALYST)]
)
def test_amount_mismatch_auto_resolves_only_within_one_minor_unit(
    diff: int, tier: EscalationTier
) -> None:
    assert _route(ExceptionCategory.AMOUNT_MISMATCH, amount_difference_minor=diff).tier is tier


def test_conditional_category_without_context_is_never_auto_resolved() -> None:
    assert _route(ExceptionCategory.AMOUNT_MISMATCH).tier is EscalationTier.TIER_2_ANALYST


@pytest.mark.parametrize(
    ("days", "tier"), [(1, EscalationTier.TIER_1_AUTO), (2, EscalationTier.TIER_2_ANALYST)]
)
def test_date_mismatch_boundary_is_t_plus_one(days: int, tier: EscalationTier) -> None:
    assert _route(ExceptionCategory.DATE_MISMATCH, days_offset=days).tier is tier


@pytest.mark.parametrize(
    ("confidence", "tier"),
    [("0.90", EscalationTier.TIER_1_AUTO), ("0.85", EscalationTier.TIER_2_ANALYST),
     ("0.70", EscalationTier.TIER_2_ANALYST)],
)  # fmt: skip
def test_reference_truncated_needs_confidence_strictly_above_threshold(
    confidence: str, tier: EscalationTier
) -> None:
    d = _route(ExceptionCategory.REFERENCE_TRUNCATED, fuzzy_confidence=Decimal(confidence))
    assert d.tier is tier


def test_direction_reversal_is_tier_4_critical_with_30_minute_deadline() -> None:
    d = _route(ExceptionCategory.DIRECTION_REVERSAL)
    assert d.tier is EscalationTier.TIER_4_COMPLIANCE
    assert d.severity is ExceptionSeverity.CRITICAL
    assert d.sla_deadline == _NOW + timedelta(minutes=30)


def test_regulatory_hold_is_tier_4() -> None:
    assert _route(ExceptionCategory.REGULATORY_HOLD).tier is EscalationTier.TIER_4_COMPLIANCE


@pytest.mark.parametrize(
    ("pct", "tier"),
    [("3.5", EscalationTier.TIER_4_COMPLIANCE), ("3.0", EscalationTier.TIER_2_ANALYST)],
)
def test_fx_variance_tier_4_only_strictly_beyond_three_percent(
    pct: str, tier: EscalationTier
) -> None:
    assert _route(ExceptionCategory.FX_VARIANCE, fx_variance_percent=Decimal(pct)).tier is tier


@pytest.mark.parametrize(
    ("count", "tier"), [(51, EscalationTier.TIER_4_COMPLIANCE), (50, EscalationTier.TIER_2_ANALYST)]
)
def test_systemic_failure_is_strictly_more_than_fifty(count: int, tier: EscalationTier) -> None:
    assert _route(ExceptionCategory.MISSING_INTERNAL, bank_batch_exception_count=count).tier is tier


def test_severity_is_independent_of_sla_ae07() -> None:
    """The AE-07 example: a Rs 50 lakh DATE_MISMATCH (8h SLA, LOW base) is CRITICAL."""
    d = _route(ExceptionCategory.DATE_MISMATCH, days_offset=3, amount_inr_minor=50 * 10 * _LAKH)
    assert d.severity is ExceptionSeverity.CRITICAL
    assert d.sla_minutes == 480


def test_naive_created_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        route(
            ExceptionContext(ExceptionCategory.MISSING_INTERNAL),
            _T,
            datetime(2026, 3, 15),  # noqa: DTZ001
        )


def test_sla_breach_escalates_one_tier_and_caps_at_four() -> None:
    deadline = _NOW
    assert (
        tier_after_sla_check(EscalationTier.TIER_2_ANALYST, deadline, _NOW)
        is EscalationTier.TIER_2_ANALYST
    )
    after = _NOW + timedelta(seconds=1)
    assert (
        tier_after_sla_check(EscalationTier.TIER_2_ANALYST, deadline, after)
        is EscalationTier.TIER_3_SENIOR
    )
    assert (
        tier_after_sla_check(EscalationTier.TIER_4_COMPLIANCE, deadline, after)
        is EscalationTier.TIER_4_COMPLIANCE
    )


def test_missing_internal_unresolved_after_four_hours_reaches_tier_3() -> None:
    """A4.2's "unresolved after 4 hours" falls out of SLA 240 min plus one tier up."""
    d = _route(ExceptionCategory.MISSING_INTERNAL)
    escalated = tier_after_sla_check(d.tier, d.sla_deadline, _NOW + timedelta(minutes=241))
    assert escalated is EscalationTier.TIER_3_SENIOR
