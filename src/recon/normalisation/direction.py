# src/recon/normalisation/direction.py
"""Direction normalisation: maps a source's raw debit/credit marker text
to the canonical Direction enum plus an is_reversal flag.

AE-12 (see docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md): MT940's :61: D/C
mark can also be RD/RC, a reversal variant the PDF's own canonical
ENUM(DR, CR) has no room for. CanonicalTransaction represents this as
Direction plus a separate is_reversal boolean (WP1's domain model)
rather than expanding the enum.

Each ingestion format uses a different vocabulary for the same two
underlying concepts (money in, money out):
  - CSV (this codebase's sample bank configs): "CR" / "DR"
  - MT940 (:61: mark): "C" / "D" / "RC" / "RD"
  - CAMT.053 (CdtDbtInd): "CRDT" / "DBIT"

A CSV bank whose column contains different text (e.g. "Credit"/"Debit"
spelled out) is not currently supported — none of this codebase's
committed bank configs require it. Extending the recognised vocabulary
means adding entries below, not restructuring this function.
"""

from __future__ import annotations

from recon.domain.enums import Direction

_CREDIT_MARKS = frozenset({"CR", "C", "CRDT"})
_DEBIT_MARKS = frozenset({"DR", "D", "DBIT"})
_REVERSAL_CREDIT_MARKS = frozenset({"RC"})
_REVERSAL_DEBIT_MARKS = frozenset({"RD"})


class DirectionNormalisationError(ValueError):
    """Raised when direction text matches none of the recognised marks
    for any supported format."""


def normalise_direction(raw_direction: str) -> tuple[Direction, bool]:
    """Returns (Direction, is_reversal)."""
    normalised = raw_direction.strip().upper()

    if normalised in _CREDIT_MARKS:
        return Direction.CR, False
    if normalised in _DEBIT_MARKS:
        return Direction.DR, False
    if normalised in _REVERSAL_CREDIT_MARKS:
        return Direction.CR, True
    if normalised in _REVERSAL_DEBIT_MARKS:
        return Direction.DR, True

    raise DirectionNormalisationError(
        f"{raw_direction!r} is not a recognised direction/credit-debit mark"
    )
