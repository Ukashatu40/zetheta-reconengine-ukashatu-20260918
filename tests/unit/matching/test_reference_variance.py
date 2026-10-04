# tests/unit/matching/test_reference_variance.py
from __future__ import annotations

import pytest

from recon.matching.scoring.fields import differs_only_in_digits


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("REF002", "REF003", True),
        ("A1", "A2", True),
        ("REF0001234567", "REF0001234568", True),
        ("REF002", "REF002", False),  # identical is exact, not variance
        ("REF002", "REF02", False),  # different length: could be truncation
        ("REFA02", "REFB02", False),  # a letter differs
        ("REF002", "REG003", False),  # a letter and a digit differ
        ("", "", False),
    ],
)
def test_differs_only_in_digits(first: str, second: str, expected: bool) -> None:
    assert differs_only_in_digits(first, second) is expected
