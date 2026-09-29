# tests/unit/matching/test_rules_base.py
"""Tests for recon.matching.rules.base.

Uses hand-written FakeRule test doubles rather than a real rule, since
this module tests the REGISTRY's behaviour (ordering, stop-at-first-match,
duplicate rejection), not any particular rule's logic — that's covered
when the first concrete rule lands.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from recon.matching.rules.base import DuplicateRuleNameError, Rule, RuleRegistry, RuleResult
from recon.persistence.models import NormalisedTransaction


def _txn(**overrides: object) -> NormalisedTransaction:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "txn_date": date(2026, 3, 15),
        "raw_transaction_id": uuid.uuid4(),
        "ingestion_file_id": uuid.uuid4(),
        "source": "INTERNAL",
        "bank_code": "HDFC",
        "format_type": "CSV",
        "config_version": "hdfc.v1",
        "txn_id": "REF001",
        "amount": Decimal("1500.00"),
        "amount_minor": 150_000,
        "currency": "INR",
        "currency_exponent": 2,
        "currency_source": "ENTRY_LEVEL",
        "direction": "CR",
        "is_reversal": False,
        "txn_timestamp_utc": datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        "txn_timestamp_original": "15-03-2026",
        "source_timezone": "Asia/Kolkata",
        "normalised_reference": "REF001",
        "match_status": "UNMATCHED",
    }
    defaults.update(overrides)
    return NormalisedTransaction(**defaults)


@dataclass
class FakeRule:
    name: str
    priority: int
    always_matches: bool
    confidence_contribution: float = 0.5

    def evaluate(
        self, internal_txn: NormalisedTransaction, external_txn: NormalisedTransaction
    ) -> RuleResult:
        return RuleResult(
            matched=self.always_matches,
            confidence_contribution=self.confidence_contribution,
            explanation=f"{self.name} evaluated (always_matches={self.always_matches})",
        )


def test_fake_rule_satisfies_the_rule_protocol() -> None:
    """Confirms FakeRule is structurally a Rule with no inheritance."""
    rule: Rule = FakeRule(name="fake", priority=1, always_matches=True)
    assert rule.name == "fake"


def test_rules_are_kept_sorted_by_priority_regardless_of_registration_order() -> None:
    registry = RuleRegistry()
    registry.register(FakeRule(name="third", priority=30, always_matches=False))
    registry.register(FakeRule(name="first", priority=10, always_matches=False))
    registry.register(FakeRule(name="second", priority=20, always_matches=False))

    assert [r.name for r in registry.rules] == ["first", "second", "third"]


def test_duplicate_rule_name_is_rejected() -> None:
    registry = RuleRegistry()
    registry.register(FakeRule(name="dup", priority=1, always_matches=False))

    with pytest.raises(DuplicateRuleNameError, match="already registered"):
        registry.register(FakeRule(name="dup", priority=2, always_matches=False))


def test_evaluate_returns_the_first_matching_rule_in_priority_order() -> None:
    registry = RuleRegistry()
    registry.register(FakeRule(name="no_match_first", priority=10, always_matches=False))
    registry.register(FakeRule(name="matches_second", priority=20, always_matches=True))
    registry.register(FakeRule(name="would_also_match_third", priority=30, always_matches=True))

    internal, external = _txn(), _txn(id=uuid.uuid4(), source="EXTERNAL")
    result = registry.evaluate(internal, external)

    assert result is not None
    matched_rule, rule_result = result
    assert matched_rule.name == "matches_second"  # not "would_also_match_third"
    assert rule_result.matched is True


def test_evaluate_returns_none_when_no_rule_matches() -> None:
    registry = RuleRegistry()
    registry.register(FakeRule(name="never", priority=10, always_matches=False))

    internal, external = _txn(), _txn(id=uuid.uuid4(), source="EXTERNAL")
    result = registry.evaluate(internal, external)

    assert result is None


def test_evaluate_on_empty_registry_returns_none() -> None:
    registry = RuleRegistry()
    internal, external = _txn(), _txn(id=uuid.uuid4(), source="EXTERNAL")

    assert registry.evaluate(internal, external) is None


def test_rules_property_returns_a_defensive_copy() -> None:
    registry = RuleRegistry()
    registry.register(FakeRule(name="a", priority=1, always_matches=False))

    snapshot = registry.rules
    snapshot.append(FakeRule(name="b", priority=2, always_matches=False))

    assert len(registry.rules) == 1  # the mutation above did not reach the registry's own list
