# src/recon/matching/subset_sum.py
"""Bounded subset-sum by dynamic programming (A3.3 split and many-to-one rules).

Finds a group of 2..max_size amounts whose sum falls inside [low, high], and
reports whether that group is UNIQUE. Uniqueness is the point: when two
different groups fit, there is no way to know which one the bank meant, so the
caller must not claim either.

The table maps (group size, running sum) to the number of distinct subsets
reaching it (capped at 2, which is all uniqueness needs) plus one witness, so
the search never enumerates subsets. Sums above `high` are pruned (amounts are
positive), and the table is capped at max_states: past that the search reports
TOO_COMPLEX rather than running unbounded. All values are integer minor units.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

DEFAULT_MAX_STATES = 200_000
_CAP = 2  # "one" versus "more than one" is all the caller needs


class SubsetOutcome(StrEnum):
    UNIQUE = "UNIQUE"
    NONE = "NONE"
    AMBIGUOUS = "AMBIGUOUS"
    TOO_COMPLEX = "TOO_COMPLEX"


@dataclass(frozen=True, slots=True)
class SubsetSearch:
    outcome: SubsetOutcome
    indices: tuple[int, ...] = ()
    total: int = 0


def find_unique_subset(
    amounts: Sequence[int],
    low: int,
    high: int,
    *,
    min_size: int = 2,
    max_size: int = 10,
    max_states: int = DEFAULT_MAX_STATES,
) -> SubsetSearch:
    if low > high:
        raise ValueError("low must not exceed high")
    if min_size < 1 or max_size < min_size:
        raise ValueError("need 1 <= min_size <= max_size")

    states: dict[tuple[int, int], tuple[int, tuple[int, ...]]] = {}
    for index, amount in enumerate(amounts):
        if amount <= 0 or amount > high:
            continue
        additions: dict[tuple[int, int], tuple[int, tuple[int, ...]]] = {}
        for (size, total), (ways, witness) in states.items():
            if size < max_size and total + amount <= high:
                _merge(additions, (size + 1, total + amount), ways, (*witness, index))
        _merge(additions, (1, amount), 1, (index,))
        for key, (ways, witness) in additions.items():
            _merge(states, key, ways, witness)
        if len(states) > max_states:
            return SubsetSearch(SubsetOutcome.TOO_COMPLEX)

    hits = [
        (ways, witness, total)
        for (size, total), (ways, witness) in states.items()
        if size >= min_size and low <= total <= high
    ]
    if not hits:
        return SubsetSearch(SubsetOutcome.NONE)
    if len(hits) > 1 or hits[0][0] >= _CAP:
        return SubsetSearch(SubsetOutcome.AMBIGUOUS)
    _, witness, total = hits[0]
    return SubsetSearch(SubsetOutcome.UNIQUE, witness, total)


def _merge(
    table: dict[tuple[int, int], tuple[int, tuple[int, ...]]],
    key: tuple[int, int],
    ways: int,
    witness: tuple[int, ...],
) -> None:
    existing = table.get(key)
    if existing is None:
        table[key] = (min(ways, _CAP), witness)
    else:
        table[key] = (min(existing[0] + ways, _CAP), existing[1])
