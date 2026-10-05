# src/recon/bench/dataset.py
"""Deterministic synthetic reconciliation dataset with known ground truth (DD-19).

The scenario mix below was fixed BEFORE any benchmark result was seen. If it
is changed afterwards, record why in docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md.
"""

from __future__ import annotations

import random
import string
import uuid
from dataclasses import dataclass
from datetime import date, timedelta

# (scenario, entities per 1000). Dict order is generation order.
SCENARIO_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("exact", 620),
    ("date_shift", 60),
    ("typo_counterparty", 60),
    ("typo_no_counterparty", 40),
    ("amount_drift", 40),
    ("internal_only", 70),
    ("external_only", 50),
    ("sequential_decoy", 60),
)
TRUE_PAIR_SCENARIOS = frozenset(
    {"exact", "date_shift", "typo_counterparty", "typo_no_counterparty", "amount_drift"}
)
_PER_THOUSAND = 1000

# Two vocabularies with no shared token, so a decoy's two names never overlap.
_NAMES_A = (
    ("ACME", "BETA", "GAMMA", "DELTA", "OMEGA", "SIGMA"),
    ("TRADING", "LOGISTICS", "FOODS", "TEXTILES"),
    ("PVT", "LLP"),
)
_NAMES_B = (
    ("ZENITH", "NOVA", "ORBIT", "PRIME", "VERTEX", "APEX"),
    ("ENERGY", "SYSTEMS", "PHARMA", "MOTORS"),
    ("INC", "CORP"),
)
_Vocabulary = tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class SyntheticRecord:
    id: uuid.UUID
    source: str
    reference: str
    amount_minor: int
    txn_date: date
    direction: str
    counterparty: str | None
    entity_id: int
    scenario: str
    pair_key: int | None  # same on both sides of a TRUE pair; None means "any match is wrong"


def allocate(size: int) -> dict[str, int]:
    """Entities per scenario, summing exactly to size (largest remainder)."""
    if size < 1:
        raise ValueError("size must be at least 1")
    counts = {name: size * weight // _PER_THOUSAND for name, weight in SCENARIO_WEIGHTS}
    remainders = {name: size * weight % _PER_THOUSAND for name, weight in SCENARIO_WEIGHTS}
    shortfall = size - sum(counts.values())
    for name in sorted(remainders, key=lambda n: (-remainders[n], n))[:shortfall]:
        counts[name] += 1
    return counts


def generate(size: int, seed: int) -> list[SyntheticRecord]:
    rng = random.Random(seed)
    used: set[str] = set()
    records: list[SyntheticRecord] = []
    entity_id = 0
    for scenario, count in allocate(size).items():
        for _ in range(count):
            records.extend(_build(scenario, entity_id, rng, used))
            entity_id += 1
    rng.shuffle(records)
    return records


def _name(rng: random.Random, vocabulary: _Vocabulary) -> str:
    return " ".join(rng.choice(part) for part in vocabulary)


def _new_id(rng: random.Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128), version=4)


def _new_reference(rng: random.Random, used: set[str]) -> str:
    while True:
        reference = "".join(rng.choices(string.ascii_uppercase, k=3)) + "".join(
            rng.choices(string.digits, k=7)
        )
        if reference not in used:
            used.add(reference)
            return reference


def _digit_typo(reference: str, used: set[str]) -> str:
    stem, last = reference[:-1], int(reference[-1])
    for step in range(1, 10):
        candidate = f"{stem}{(last + step) % 10}"
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise RuntimeError(
        "no free reference variant"
    )  # nine taken neighbours: not reachable in practice


def _build(
    scenario: str, entity_id: int, rng: random.Random, used: set[str]
) -> list[SyntheticRecord]:
    base_date = date(2026, 3, 1) + timedelta(days=rng.randrange(28))
    amount = rng.randrange(10_000, 10_000_000)
    direction = rng.choice(("CR", "DR"))
    vocabulary = rng.choice((_NAMES_A, _NAMES_B))
    other_vocabulary = _NAMES_B if vocabulary is _NAMES_A else _NAMES_A
    reference = _new_reference(rng, used)
    pair_key = entity_id if scenario in TRUE_PAIR_SCENARIOS else None

    internal_name: str | None = (
        None if scenario == "typo_no_counterparty" else _name(rng, vocabulary)
    )
    external_name = (
        _name(rng, other_vocabulary) if scenario == "sequential_decoy" else internal_name
    )

    def make(
        source: str, ref: str, amount_minor: int, day: date, counterparty: str | None
    ) -> SyntheticRecord:
        return SyntheticRecord(
            id=_new_id(rng),
            source=source,
            reference=ref,
            amount_minor=amount_minor,
            txn_date=day,
            direction=direction,
            counterparty=counterparty,
            entity_id=entity_id,
            scenario=scenario,
            pair_key=pair_key,
        )

    out: list[SyntheticRecord] = []
    if scenario != "external_only":
        out.append(make("INTERNAL", reference, amount, base_date, internal_name))
    if scenario in ("exact", "external_only"):
        out.append(make("EXTERNAL", reference, amount, base_date, external_name))
    elif scenario == "date_shift":
        out.append(
            make("EXTERNAL", reference, amount, base_date + timedelta(days=1), external_name)
        )
    elif scenario == "amount_drift":
        out.append(make("EXTERNAL", reference, amount + 1, base_date, external_name))
    elif scenario in ("typo_counterparty", "typo_no_counterparty", "sequential_decoy"):
        out.append(make("EXTERNAL", _digit_typo(reference, used), amount, base_date, external_name))
    return out
