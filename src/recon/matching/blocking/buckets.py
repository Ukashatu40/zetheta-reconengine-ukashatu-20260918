# src/recon/matching/blocking/buckets.py
"""Bucketing functions: cheap, deterministic groupings used as blocking
keys. None of these do string-similarity comparison — they exist purely
to narrow the candidate space before that expensive work happens.
"""

from __future__ import annotations

from datetime import date


def amount_bucket(amount_minor: int, bucket_width_minor: int) -> int:
    """Buckets an amount (in minor units) into a fixed-width band, so two
    amounts within the same tolerance window usually land in the same or
    an adjacent bucket. bucket_width_minor is caller-supplied (derived
    from a bank's configured amount tolerance), never a project-wide
    constant — a ±0.01 tolerance and a ±1.5% FX tolerance need very
    different bucket widths.
    """
    if bucket_width_minor <= 0:
        raise ValueError("bucket_width_minor must be positive")
    return amount_minor // bucket_width_minor


def date_bucket(txn_date: date, window_days: int) -> int:
    """Buckets a date into a fixed-width window measured in days since
    the epoch. window_days is caller-supplied per rail (A5.3's settlement
    cycle table gives very different windows for UPI vs RTGS vs
    cross-border SWIFT) — never a hardcoded 2 days.
    """
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    return txn_date.toordinal() // window_days


def reference_prefix(normalised_reference: str, prefix_length: int) -> str:
    """First N characters of a normalised reference — cheap enough to
    use directly as a dict key with no hashing step of its own."""
    return normalised_reference[:prefix_length]


def counterparty_key(counterparty_name_normalised: str | None, prefix_length: int) -> str | None:
    """First N characters of a normalised counterparty name. Returns None
    when the source has no counterparty name at all (e.g. MT940 — see
    AE-01), signalling to the caller that Pass C is not usable for this
    transaction, rather than blocking on an empty string that would
    collide every counterparty-less transaction into one giant bucket.
    """
    if not counterparty_name_normalised:
        return None
    return counterparty_name_normalised[:prefix_length]
