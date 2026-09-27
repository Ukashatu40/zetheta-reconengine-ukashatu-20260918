# src/recon/matching/blocking/candidates.py
"""Candidate generation for fuzzy matching (A3.1's blocking requirement).

Three independent blocking passes, unioned and deduplicated — not one
conjunctive filter. See this module's own docstring section below for
why a single AND-ed block (as Day 3 literally describes) would make
split/netted matching and reference-led matching structurally
unreachable.

Pass A (amount-led): (bank_code, currency, direction, amount_bucket,
    date_bucket) — the common case: similar amount, similar timing.
Pass B (reference-led): (bank_code, currency, direction,
    reference_prefix) — catches cases where amount or date drifted
    (a fee deducted, a partial settlement) but the reference is intact.
Pass C (counterparty-led): (bank_code, currency, direction,
    counterparty_key, date_bucket) — catches cases where the reference
    is garbled but counterparty and rough timing agree. Skipped entirely
    for transactions with no counterparty name (e.g. MT940 sources).
"""

from __future__ import annotations

from dataclasses import dataclass

from recon.matching.blocking.buckets import (
    amount_bucket,
    counterparty_key,
    date_bucket,
    reference_prefix,
)
from recon.persistence.models import NormalisedTransaction


@dataclass(frozen=True, slots=True)
class BlockingConfig:
    amount_bucket_width_minor: int
    date_window_days: int
    reference_prefix_length: int = 6
    counterparty_prefix_length: int = 8
    max_candidates_per_transaction: int = 50


@dataclass(frozen=True, slots=True)
class CandidateSet:
    external_transaction_id: object  # uuid.UUID — left loosely typed here to avoid
    # a uuid import solely for an annotation
    candidates: list[NormalisedTransaction]
    truncated: bool


class CandidateGenerator:
    def __init__(
        self, internal_candidates: list[NormalisedTransaction], config: BlockingConfig
    ) -> None:
        self._config = config
        self._by_amount_bucket = self._index_by_amount_and_date(internal_candidates)
        self._by_reference_prefix = self._index_by_reference(internal_candidates)
        self._by_counterparty = self._index_by_counterparty_and_date(internal_candidates)

    def generate(self, external_txn: NormalisedTransaction) -> CandidateSet:
        seen_ids: set[object] = set()
        combined: list[NormalisedTransaction] = []

        for candidate in self._pass_a(external_txn):
            if candidate.id not in seen_ids:
                seen_ids.add(candidate.id)
                combined.append(candidate)

        for candidate in self._pass_b(external_txn):
            if candidate.id not in seen_ids:
                seen_ids.add(candidate.id)
                combined.append(candidate)

        for candidate in self._pass_c(external_txn):
            if candidate.id not in seen_ids:
                seen_ids.add(candidate.id)
                combined.append(candidate)

        truncated = len(combined) > self._config.max_candidates_per_transaction
        if truncated:
            combined = combined[: self._config.max_candidates_per_transaction]

        return CandidateSet(
            external_transaction_id=external_txn.id, candidates=combined, truncated=truncated
        )

    def _pass_a(self, external_txn: NormalisedTransaction) -> list[NormalisedTransaction]:
        key = self._amount_date_key(external_txn)
        return self._by_amount_bucket.get(key, [])

    def _pass_b(self, external_txn: NormalisedTransaction) -> list[NormalisedTransaction]:
        if not external_txn.normalised_reference:
            return []
        key = (
            external_txn.bank_code,
            external_txn.currency,
            external_txn.direction,
            reference_prefix(
                external_txn.normalised_reference, self._config.reference_prefix_length
            ),
        )
        return self._by_reference_prefix.get(key, [])

    def _pass_c(self, external_txn: NormalisedTransaction) -> list[NormalisedTransaction]:
        cp_key = counterparty_key(
            external_txn.counterparty_name_normalised, self._config.counterparty_prefix_length
        )
        if cp_key is None:
            return []
        key = (
            external_txn.bank_code,
            external_txn.currency,
            external_txn.direction,
            cp_key,
            date_bucket(external_txn.txn_date, self._config.date_window_days),
        )
        return self._by_counterparty.get(key, [])

    def _amount_date_key(self, txn: NormalisedTransaction) -> tuple[object, ...]:
        return (
            txn.bank_code,
            txn.currency,
            txn.direction,
            amount_bucket(txn.amount_minor, self._config.amount_bucket_width_minor),
            date_bucket(txn.txn_date, self._config.date_window_days),
        )

    def _index_by_amount_and_date(
        self, candidates: list[NormalisedTransaction]
    ) -> dict[tuple[object, ...], list[NormalisedTransaction]]:
        index: dict[tuple[object, ...], list[NormalisedTransaction]] = {}
        for txn in candidates:
            index.setdefault(self._amount_date_key(txn), []).append(txn)
        return index

    def _index_by_reference(
        self, candidates: list[NormalisedTransaction]
    ) -> dict[tuple[object, ...], list[NormalisedTransaction]]:
        index: dict[tuple[object, ...], list[NormalisedTransaction]] = {}
        for txn in candidates:
            if not txn.normalised_reference:
                continue
            key = (
                txn.bank_code,
                txn.currency,
                txn.direction,
                reference_prefix(txn.normalised_reference, self._config.reference_prefix_length),
            )
            index.setdefault(key, []).append(txn)
        return index

    def _index_by_counterparty_and_date(
        self, candidates: list[NormalisedTransaction]
    ) -> dict[tuple[object, ...], list[NormalisedTransaction]]:
        index: dict[tuple[object, ...], list[NormalisedTransaction]] = {}
        for txn in candidates:
            cp_key = counterparty_key(
                txn.counterparty_name_normalised, self._config.counterparty_prefix_length
            )
            if cp_key is None:
                continue
            key = (
                txn.bank_code,
                txn.currency,
                txn.direction,
                cp_key,
                date_bucket(txn.txn_date, self._config.date_window_days),
            )
            index.setdefault(key, []).append(txn)
        return index
