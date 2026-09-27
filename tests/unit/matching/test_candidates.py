# tests/unit/matching/test_candidates.py
"""Tests for recon.matching.blocking.candidates.CandidateGenerator.

Uses hand-built NormalisedTransaction objects directly (not persisted) —
CandidateGenerator has no database dependency, so its tests shouldn't
either. The FK-related fixture bug from Increment 2's integration tests
doesn't apply here: nothing here is ever flushed to a session.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from recon.matching.blocking.candidates import BlockingConfig, CandidateGenerator
from recon.persistence.models import NormalisedTransaction


def _txn(**overrides: object) -> NormalisedTransaction:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "txn_date": date(2026, 3, 15),
        "raw_transaction_id": uuid.uuid4(),
        "ingestion_file_id": uuid.uuid4(),
        "source": "INTERNAL",
        "bank_code": "HDFC",
        "format_type": "CSV",
        "config_version": "hdfc.v1",
        "txn_id": "REF001",
        "amount": Decimal("1500.00"),
        "amount_minor": 150_000,
        "currency": "INR",
        "currency_exponent": 2,
        "currency_source": "ENTRY_LEVEL",
        "direction": "CR",
        "is_reversal": False,
        "txn_timestamp_utc": datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        "txn_timestamp_original": "15-03-2026",
        "source_timezone": "Asia/Kolkata",
        "normalised_reference": "REF001",
        "counterparty_name_normalised": None,
        "match_status": "UNMATCHED",
    }
    defaults.update(overrides)
    return NormalisedTransaction(**defaults)


def _config(**overrides: object) -> BlockingConfig:
    defaults: dict[str, object] = {
        "amount_bucket_width_minor": 10_000,
        "date_window_days": 2,
    }
    defaults.update(overrides)
    return BlockingConfig(**defaults)  # type: ignore[arg-type]


def test_pass_a_matches_on_similar_amount_and_date() -> None:
    internal = _txn(
        normalised_reference="DIFFERENT_REF"
    )  # reference deliberately won't match Pass B
    external = _txn(id=uuid.uuid4(), source="EXTERNAL", normalised_reference="ALSO_DIFFERENT")

    generator = CandidateGenerator([internal], _config())
    result = generator.generate(external)

    assert len(result.candidates) == 1
    assert result.candidates[0].id == internal.id
    assert result.truncated is False


def test_pass_b_matches_on_reference_prefix_even_with_different_amount() -> None:
    """The reason a single conjunctive block is wrong: this candidate
    would NEVER be found by Pass A's amount bucket alone."""
    internal = _txn(amount_minor=999_999_999, normalised_reference="REF0001234567")
    external = _txn(
        id=uuid.uuid4(),
        source="EXTERNAL",
        amount_minor=1,  # wildly different amount
        normalised_reference="REF0001999999",  # same 6-char prefix
    )

    generator = CandidateGenerator([internal], _config())
    result = generator.generate(external)

    assert len(result.candidates) == 1
    assert result.candidates[0].id == internal.id


def test_pass_c_matches_on_counterparty_even_with_garbled_reference() -> None:
    internal = _txn(
        normalised_reference="XXXGARBLEDXXX",
        counterparty_name_normalised="ACME CORPORATION",
    )
    external = _txn(
        id=uuid.uuid4(),
        source="EXTERNAL",
        normalised_reference="COMPLETELYDIFFERENT",
        counterparty_name_normalised="ACME CORPORATION",
    )

    generator = CandidateGenerator([internal], _config())
    result = generator.generate(external)

    assert len(result.candidates) == 1
    assert result.candidates[0].id == internal.id


def test_candidate_found_by_multiple_passes_is_not_duplicated() -> None:
    internal = _txn()  # matches Pass A (same amount/date) AND Pass B (same reference prefix)
    external = _txn(id=uuid.uuid4(), source="EXTERNAL")

    generator = CandidateGenerator([internal], _config())
    result = generator.generate(external)

    assert len(result.candidates) == 1  # not 2, despite matching two passes


def test_no_matching_candidate_in_any_pass_returns_empty() -> None:
    internal = _txn(
        amount_minor=999_999_999,
        normalised_reference="TOTALLYDIFFERENT",
        txn_date=date(2020, 1, 1),
    )
    external = _txn(id=uuid.uuid4(), source="EXTERNAL")

    generator = CandidateGenerator([internal], _config())
    result = generator.generate(external)

    assert result.candidates == []
    assert result.truncated is False


def test_candidates_exceeding_the_cap_are_truncated_and_flagged() -> None:
    internal_candidates = [_txn(id=uuid.uuid4()) for _ in range(60)]  # all share the same bucket
    external = _txn(id=uuid.uuid4(), source="EXTERNAL")

    generator = CandidateGenerator(internal_candidates, _config(max_candidates_per_transaction=50))
    result = generator.generate(external)

    assert len(result.candidates) == 50
    assert result.truncated is True


def test_counterparty_less_transaction_is_still_matchable_via_other_passes() -> None:
    """AE-01: MT940 sources have no counterparty name. Pass C being
    unusable must not prevent Pass A or Pass B from working."""
    internal = _txn(counterparty_name_normalised=None)
    external = _txn(id=uuid.uuid4(), source="EXTERNAL", counterparty_name_normalised=None)

    generator = CandidateGenerator([internal], _config())
    result = generator.generate(external)

    assert len(result.candidates) == 1
