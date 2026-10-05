# tests/unit/bench/test_dataset.py
from __future__ import annotations

from collections import defaultdict

import pytest

from recon.bench.dataset import SCENARIO_WEIGHTS, SyntheticRecord, allocate, generate
from recon.matching.scoring.fields import differs_only_in_digits


def _entities(records: list[SyntheticRecord]) -> dict[int, list[SyntheticRecord]]:
    grouped: dict[int, list[SyntheticRecord]] = defaultdict(list)
    for record in records:
        grouped[record.entity_id].append(record)
    return grouped


def _of(records: list[SyntheticRecord], scenario: str) -> list[dict[str, SyntheticRecord]]:
    return [
        {r.source: r for r in group}
        for group in _entities(records).values()
        if group[0].scenario == scenario
    ]


@pytest.mark.parametrize("size", [1, 7, 33, 999, 1001, 12345])
def test_allocation_sums_to_the_requested_size(size: int) -> None:
    assert sum(allocate(size).values()) == size


def test_allocation_at_1000_equals_the_weights() -> None:
    assert allocate(1000) == dict(SCENARIO_WEIGHTS)


def test_allocation_rejects_a_non_positive_size() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        allocate(0)


def test_generation_is_deterministic_per_seed_and_differs_across_seeds() -> None:
    assert generate(200, 1) == generate(200, 1)
    assert generate(200, 1) != generate(200, 2)


def test_record_counts_at_1000() -> None:
    records = generate(1000, 42)
    assert sum(1 for r in records if r.source == "INTERNAL") == 950
    assert sum(1 for r in records if r.source == "EXTERNAL") == 930
    assert len(_entities(records)) == 1000


def test_ids_are_unique_and_references_are_unique_per_side() -> None:
    records = generate(1000, 42)
    assert len({r.id for r in records}) == len(records)
    for source in ("INTERNAL", "EXTERNAL"):
        refs = [r.reference for r in records if r.source == source]
        assert len(refs) == len(set(refs))


def test_pair_keys_are_set_only_for_true_pairs_and_shared_by_both_sides() -> None:
    for group in _entities(generate(500, 3)).values():
        keys = {r.pair_key for r in group}
        if group[0].scenario in {
            "exact",
            "date_shift",
            "typo_counterparty",
            "typo_no_counterparty",
            "amount_drift",
        }:
            assert keys == {group[0].entity_id}
            assert len(group) == 2
        else:
            assert keys == {None}


def test_the_typo_scenarios_differ_only_in_the_last_digit() -> None:
    records = generate(1000, 42)
    for scenario in ("typo_counterparty", "typo_no_counterparty", "sequential_decoy"):
        pairs = _of(records, scenario)
        assert pairs
        for pair in pairs:
            internal, external = pair["INTERNAL"], pair["EXTERNAL"]
            assert differs_only_in_digits(internal.reference, external.reference)
            assert internal.reference[:-1] == external.reference[:-1]


def test_counterparty_presence_matches_each_scenario() -> None:
    records = generate(1000, 42)
    for pair in _of(records, "typo_no_counterparty"):
        assert pair["INTERNAL"].counterparty is None and pair["EXTERNAL"].counterparty is None
    for scenario in ("exact", "date_shift", "typo_counterparty", "amount_drift"):
        for pair in _of(records, scenario):
            assert pair["INTERNAL"].counterparty is not None
            assert pair["INTERNAL"].counterparty == pair["EXTERNAL"].counterparty


def test_decoys_share_amount_date_and_direction_but_no_counterparty_token() -> None:
    for pair in _of(generate(1000, 42), "sequential_decoy"):
        internal, external = pair["INTERNAL"], pair["EXTERNAL"]
        assert (internal.amount_minor, internal.txn_date, internal.direction) == (
            external.amount_minor,
            external.txn_date,
            external.direction,
        )
        assert internal.counterparty and external.counterparty
        assert not set(internal.counterparty.split()) & set(external.counterparty.split())


def test_date_shift_and_amount_drift_differ_exactly_as_named() -> None:
    records = generate(1000, 42)
    for pair in _of(records, "date_shift"):
        assert (pair["EXTERNAL"].txn_date - pair["INTERNAL"].txn_date).days == 1
        assert pair["EXTERNAL"].amount_minor == pair["INTERNAL"].amount_minor
    for pair in _of(records, "amount_drift"):
        assert pair["EXTERNAL"].amount_minor - pair["INTERNAL"].amount_minor == 1
        assert pair["EXTERNAL"].reference == pair["INTERNAL"].reference
