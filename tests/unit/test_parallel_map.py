"""The thread-pool map the backlog endpoint uses to recover banks in parallel."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from recon.api.v1.runs import get_parallel_map

_parallel: Any = (
    get_parallel_map()
)  # the real map; work functions here are not bank backlog results


def test_results_come_back_in_input_order_even_when_work_finishes_out_of_order() -> None:
    delays = {"A": 0.15, "B": 0.0, "C": 0.05}

    def work(bank: str) -> str:
        time.sleep(delays[bank])
        return bank.lower()

    assert list(_parallel(work, ["A", "B", "C"])) == ["a", "b", "c"]


def test_banks_really_run_concurrently() -> None:
    barrier = threading.Barrier(3, timeout=5)

    def work(bank: str) -> str:
        barrier.wait()  # only passes if all three are in flight at once
        return bank

    assert sorted(_parallel(work, ["A", "B", "C"])) == ["A", "B", "C"]


def test_an_empty_bank_list_is_a_no_op() -> None:
    assert list(_parallel(lambda b: b, [])) == []


def test_a_failure_in_one_bank_propagates() -> None:
    def work(bank: str) -> str:
        if bank == "B":
            raise RuntimeError("bank B exploded")
        return bank

    with pytest.raises(RuntimeError, match="bank B exploded"):
        list(_parallel(work, ["A", "B"]))
