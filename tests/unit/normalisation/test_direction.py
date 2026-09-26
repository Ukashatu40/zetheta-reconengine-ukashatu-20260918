# tests/unit/normalisation/test_direction.py
from __future__ import annotations

import pytest

from recon.domain.enums import Direction
from recon.normalisation.direction import DirectionNormalisationError, normalise_direction


@pytest.mark.parametrize(
    ("raw", "expected_direction", "expected_reversal"),
    [
        ("CR", Direction.CR, False),
        ("cr", Direction.CR, False),
        ("C", Direction.CR, False),
        ("CRDT", Direction.CR, False),
        ("DR", Direction.DR, False),
        ("D", Direction.DR, False),
        ("DBIT", Direction.DR, False),
        ("RC", Direction.CR, True),
        ("RD", Direction.DR, True),
    ],
)
def test_normalise_direction_recognises_all_format_vocabularies(
    raw: str, expected_direction: Direction, expected_reversal: bool
) -> None:
    direction, is_reversal = normalise_direction(raw)
    assert direction == expected_direction
    assert is_reversal == expected_reversal


def test_whitespace_is_stripped() -> None:
    direction, _ = normalise_direction("  CR  ")
    assert direction == Direction.CR


def test_unrecognised_mark_raises() -> None:
    with pytest.raises(DirectionNormalisationError, match="not a recognised"):
        normalise_direction("SIDEWAYS")
