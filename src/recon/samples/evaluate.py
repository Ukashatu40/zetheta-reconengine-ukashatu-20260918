# src/recon/samples/evaluate.py
"""Compares what the engine did with a SampleSet against its manifest (A8.3).

For every planted scenario it finds the scenario's transactions by reference, then
checks the match outcome and the set of exception categories attached to them. Any
deviation is reported with the scenario id, so a failing gate says exactly which
planted case drifted and how."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.normalisation.reference import clean_reference
from recon.persistence.models import MatchResult, NormalisedTransaction, ReconException

_MATCHED = ("AUTO_MATCHED", "CONFIRMED")


@dataclass(frozen=True, slots=True)
class Deviation:
    scenario_id: str
    expected: str
    actual: str

    def __str__(self) -> str:
        return f"{self.scenario_id}: expected {self.expected}, got {self.actual}"


@dataclass(slots=True)
class Evaluation:
    scenarios: int = 0
    deviations: list[Deviation] = field(default_factory=list)
    by_kind: dict[str, dict[str, int]] = field(default_factory=dict)
    exception_counts: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.deviations

    def accuracy(self) -> float:
        return 1.0 if not self.scenarios else 1 - len(self.deviations) / self.scenarios


def _ids_in(value: Any) -> set[str]:
    """Every string anywhere inside an exception's affected_records."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {i for v in value.values() for i in _ids_in(v)}
    if isinstance(value, list):
        return {i for v in value for i in _ids_in(v)}
    return set()


def evaluate(session: Session, manifest: dict[str, Any]) -> Evaluation:
    txns = session.scalars(select(NormalisedTransaction)).all()
    by_key: dict[tuple[str, str, str], list[uuid.UUID]] = defaultdict(list)
    for t in txns:
        by_key[(t.bank_code, t.source, t.normalised_reference or "")].append(t.id)

    matches = session.scalars(select(MatchResult)).all()
    exceptions = session.scalars(select(ReconException)).all()
    exception_ids = [(e.category, _ids_in(e.affected_records)) for e in exceptions]
    format_errors: dict[str, int] = defaultdict(int)
    for e in exceptions:
        if e.category == "FORMAT_ERROR":
            format_errors[e.bank_code] += 1

    result = Evaluation()
    for e in exceptions:
        result.exception_counts[e.category] = result.exception_counts.get(e.category, 0) + 1

    for scenario in manifest["scenarios"]:
        result.scenarios += 1
        bank = scenario["bank"]
        ids = {
            str(i)
            for source, refs in (
                ("INTERNAL", scenario["internal_refs"]),
                ("EXTERNAL", scenario["external_refs"]),
            )
            for ref in refs
            for i in by_key.get((bank, source, clean_reference(ref)), [])
        }
        actual_match = _actual_match(ids, matches)
        actual_categories = {cat for cat, affected in exception_ids if affected & ids}
        expected_categories = set(scenario["expect_exceptions"])
        if "FORMAT_ERROR" in expected_categories and not ids:
            # a corrupt row never becomes a transaction; the file-level exception stands in
            expected_categories.discard("FORMAT_ERROR")
            if not format_errors[bank]:
                result.deviations.append(Deviation(scenario["id"], "a FORMAT_ERROR", "none"))
        stats = result.by_kind.setdefault(scenario["kind"], {"total": 0, "ok": 0})
        stats["total"] += 1
        ok = True
        if not _match_ok(scenario["expect_match"], actual_match):
            result.deviations.append(
                Deviation(
                    scenario["id"],
                    f"match {scenario['expect_match']}",
                    f"match {sorted(actual_match) or None}",
                )
            )
            ok = False
        if expected_categories != actual_categories:
            result.deviations.append(
                Deviation(
                    scenario["id"],
                    f"exceptions {sorted(expected_categories)}",
                    f"exceptions {sorted(actual_categories)}",
                )
            )
            ok = False
        stats["ok"] += int(ok)
    return result


def _actual_match(ids: set[str], matches: Sequence[MatchResult]) -> set[str]:
    """Labels for what happened to a scenario's transactions: 'EXACT', 'FUZZY', ...,
    'CONFIRMED' for a review pair an auto-resolver confirmed, 'REVIEW' while one waits."""
    labels: set[str] = set()
    for m in matches:
        if str(m.internal_transaction_id) not in ids and str(m.external_transaction_id) not in ids:
            continue
        if m.status in _MATCHED:
            labels.add(m.match_type)
            if m.status == "CONFIRMED":
                labels.add("CONFIRMED")
        elif m.status == "PENDING_REVIEW":
            labels.add("REVIEW")
    return labels


def _match_ok(expected: str | None, actual: set[str]) -> bool:
    if expected is None:
        return not actual
    if expected == "TOLERANCE":
        return bool(actual & {"FUZZY", "RULE"})
    if expected == "REVIEW":
        return actual == {"REVIEW"}
    return expected in actual
