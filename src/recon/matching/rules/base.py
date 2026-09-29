# src/recon/matching/rules/base.py
"""Rule engine skeleton (A3.3): an extensible rule abstraction instead of
a chain of if/elif statements, per A3.3's own instruction ("avoid a giant
chain of if/elif statements... use a rule abstraction or registry").

Each Rule has a name, a priority (lower runs first), and evaluate(),
which returns a RuleResult carrying whether it matched, its confidence
contribution, and a human-readable explanation (A3.3 asks rules to have
"a rule ID, name, priority, conditions, evaluation, result, confidence
contribution, explanation").

RuleRegistry runs rules in priority order and stops at the first match.
Rules are not blended — this is a sequential-predicate system, not a
second weighted-sum layer on top of fuzzy matching's scoring.

No concrete rule is defined in this module. Amount Tolerance and Date
Offset (WP4's next two increments) are the first real Rule
implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from recon.persistence.models import NormalisedTransaction


@dataclass(frozen=True, slots=True)
class RuleResult:
    matched: bool
    confidence_contribution: float
    explanation: str


class Rule(Protocol):
    name: str
    priority: int

    def evaluate(
        self, internal_txn: NormalisedTransaction, external_txn: NormalisedTransaction
    ) -> RuleResult: ...


class DuplicateRuleNameError(ValueError):
    """Raised when two rules are registered with the same name. Rule
    names are used elsewhere (match_results.rule_id, once a rule-based
    strategy exists) as the audit trail's pointer to which rule produced
    a match — a duplicate name would make that pointer ambiguous."""


class RuleRegistry:
    def __init__(self) -> None:
        self._rules: list[Rule] = []

    def register(self, rule: Rule) -> None:
        if any(existing.name == rule.name for existing in self._rules):
            raise DuplicateRuleNameError(f"a rule named {rule.name!r} is already registered")
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority)

    @property
    def rules(self) -> list[Rule]:
        """A defensive copy — callers must not mutate the registry's
        internal ordering by holding onto this list."""
        return list(self._rules)

    def evaluate(
        self, internal_txn: NormalisedTransaction, external_txn: NormalisedTransaction
    ) -> tuple[Rule, RuleResult] | None:
        """Runs registered rules in priority order, returning the first
        (rule, result) pair where result.matched is True. Returns None
        if no rule matches — the caller (a future RuleBasedMatchingStrategy)
        then falls through to whatever level comes after rule-based
        matching, exactly as exact falls through to fuzzy today."""
        for rule in self._rules:
            result = rule.evaluate(internal_txn, external_txn)
            if result.matched:
                return rule, result
        return None
