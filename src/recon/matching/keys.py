# src/recon/matching/keys.py
"""The exact-matching composite key.

AE-16 (docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md — see the Phase 0
report's C16): the PDF states this key two incompatible ways (A3.1 says
reference+amount+currency+date; Day 3 says txn_id+amount+currency+
direction). Neither txn_id nor a fixed date-inclusion is safe as stated
— txn_id is not guaranteed unique across banks (AE-15), and requiring an
exact date match would make the key too strict for legitimate T+1
settlement timing.

The key actually used: (normalised_reference, amount_minor, currency,
direction). Reference comes from the NORMALISED field (WP3's
clean_reference output), never raw txn_id. Amount uses exact integer
minor units, never Decimal equality. txn_date is deliberately excluded
from the key — it is used only as a secondary disambiguation signal when
a hash bucket contains more than one candidate (R47's "hash collision
handling"), not as a key component.
"""

from __future__ import annotations

from dataclasses import dataclass

from recon.persistence.models import NormalisedTransaction


@dataclass(frozen=True, slots=True)
class ExactMatchKey:
    normalised_reference: str
    amount_minor: int
    currency: str
    direction: str


def build_key(txn: NormalisedTransaction) -> ExactMatchKey | None:
    """Returns None when a transaction lacks a usable normalised
    reference — such a row cannot participate in exact matching at all
    and is left for a later matching level (fuzzy, rule-based) to
    consider instead of being force-matched on an empty key."""
    if not txn.normalised_reference or not txn.normalised_reference.strip():
        return None
    return ExactMatchKey(
        normalised_reference=txn.normalised_reference,
        amount_minor=txn.amount_minor,
        currency=txn.currency,
        direction=txn.direction,
    )
