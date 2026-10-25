"""Reference stems link the parts of a split or netted settlement."""

from __future__ import annotations

import pytest

from recon.matching.strategies.group import reference_stem


@pytest.mark.parametrize(
    ("reference", "stem"),
    [
        ("PAY-100-1", "PAY-100"),
        ("pay-100-2", "PAY-100"),
        ("BATCH_20260315_007", "BATCH_20260315"),
        ("NET/20260315/001", "NET/20260315"),
        ("PAY-100", None),  # "PAY" is below the minimum stem length
        ("PAY100", None),  # no separator
        ("PAY-100-1234567", None),  # the suffix is too long to be a sequence number
        ("", None),
        (None, None),
    ],
)
def test_reference_stem(reference: str | None, stem: str | None) -> None:
    assert reference_stem(reference, 4) == stem
