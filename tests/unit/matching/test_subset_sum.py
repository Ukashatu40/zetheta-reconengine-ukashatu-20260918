"""Bounded subset-sum: unique, none, ambiguous, too complex."""

from __future__ import annotations

import random
from itertools import combinations

import pytest

from recon.matching.subset_sum import SubsetOutcome, find_unique_subset


def test_a_unique_group_is_found_with_its_indices() -> None:
    result = find_unique_subset([100, 250, 400, 999], 650, 650)
    assert result.outcome is SubsetOutcome.UNIQUE
    assert sorted(result.indices) == [1, 2]
    assert result.total == 650


def test_no_group_reaching_the_target_is_none() -> None:
    assert find_unique_subset([100, 200, 300], 1000, 1000).outcome is SubsetOutcome.NONE


def test_two_different_groups_with_the_same_sum_are_ambiguous() -> None:
    # 100+400 == 200+300
    assert find_unique_subset([100, 200, 300, 400], 500, 500).outcome is SubsetOutcome.AMBIGUOUS


def test_two_equal_amounts_make_the_choice_ambiguous() -> None:
    assert find_unique_subset([300, 300, 600], 900, 900).outcome is SubsetOutcome.AMBIGUOUS


def test_a_single_amount_is_not_a_group() -> None:
    assert find_unique_subset([500, 7], 500, 500).outcome is SubsetOutcome.NONE
    assert find_unique_subset([500, 7], 500, 500, min_size=1).outcome is SubsetOutcome.UNIQUE


def test_the_tolerance_window_accepts_a_near_sum() -> None:
    result = find_unique_subset([100, 250, 400], 649, 651)
    assert (result.outcome, sorted(result.indices), result.total) == (
        SubsetOutcome.UNIQUE,
        [1, 2],
        650,
    )


def test_two_sums_inside_the_window_are_ambiguous() -> None:
    assert find_unique_subset([100, 250, 400, 401], 649, 651).outcome is SubsetOutcome.AMBIGUOUS


def test_the_group_size_cap_is_respected() -> None:
    assert find_unique_subset([1, 1, 1, 1, 1], 5, 5, max_size=4).outcome is SubsetOutcome.NONE
    assert find_unique_subset([1, 2, 3, 4, 5], 15, 15, max_size=5).outcome is SubsetOutcome.UNIQUE


def test_non_positive_and_oversized_amounts_are_ignored() -> None:
    result = find_unique_subset([0, -5, 10_000, 40, 60], 100, 100)
    assert result.outcome is SubsetOutcome.UNIQUE
    assert sorted(result.indices) == [3, 4]


def test_the_state_cap_reports_too_complex_instead_of_running_on() -> None:
    result = find_unique_subset(list(range(1, 40)), 10**9, 10**9, max_states=50)
    assert result.outcome is SubsetOutcome.TOO_COMPLEX


def test_invalid_arguments_raise() -> None:
    with pytest.raises(ValueError, match="low"):
        find_unique_subset([1], 5, 4)
    with pytest.raises(ValueError, match="min_size"):
        find_unique_subset([1], 1, 1, min_size=3, max_size=2)


@pytest.mark.parametrize("seed", range(25))
def test_agrees_with_brute_force_on_random_inputs(seed: int) -> None:
    rng = random.Random(seed)
    amounts = [rng.randint(1, 60) for _ in range(rng.randint(2, 9))]
    target = rng.randint(2, 120)
    low, high = target - rng.randint(0, 2), target + rng.randint(0, 2)

    hits = [
        subset
        for size in range(2, min(len(amounts), 6) + 1)
        for subset in combinations(range(len(amounts)), size)
        if low <= sum(amounts[i] for i in subset) <= high
    ]
    result = find_unique_subset(amounts, low, high, max_size=6)

    if not hits:
        assert result.outcome is SubsetOutcome.NONE
    elif len(hits) == 1:
        assert result.outcome is SubsetOutcome.UNIQUE
        assert tuple(sorted(result.indices)) == hits[0]
    else:
        assert result.outcome is SubsetOutcome.AMBIGUOUS
