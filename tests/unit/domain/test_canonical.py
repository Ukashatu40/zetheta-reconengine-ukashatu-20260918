# tests/unit/domain/test_canonical.py
"""Tests for recon.domain.canonical.CanonicalTransaction.

Focus is on the constraints that make the corrections in
docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md structurally enforced: consistent
amount/amount_minor, sign-on-direction not sign-on-amount, reversal fields
only valid together.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from recon.domain.canonical import CanonicalTransaction
from recon.domain.enums import CurrencySource, Direction, SourceFormat, TransactionSource


def _base_kwargs() -> dict[str, object]:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "source": TransactionSource.EXTERNAL,
        "bank_code": "HDFC",
        "format_type": SourceFormat.CSV,
        "ingestion_file_id": "22222222-2222-2222-2222-222222222222",
        "config_version": "hdfc.v1",
        "txn_id": "HDFC-REF-000123",
        "txn_date": date(2026, 3, 15),
        "amount": Decimal("1500.00"),
        "amount_minor": 150_000,
        "currency": "INR",
        "currency_exponent_value": 2,
        "currency_source": CurrencySource.ENTRY_LEVEL,
        "direction": Direction.CR,
        "txn_timestamp_utc": datetime(2026, 3, 15, 18, 0, 0, tzinfo=UTC),
        "txn_timestamp_original": "2026-03-15T23:30:00+05:30",
        "source_timezone": "Asia/Kolkata",
    }


def test_valid_canonical_transaction_constructs() -> None:
    txn = CanonicalTransaction(**_base_kwargs())
    assert txn.amount_minor == 150_000
    assert txn.direction == Direction.CR


def test_negative_amount_is_rejected() -> None:
    """AE-05: sign lives on direction, never on amount."""
    kwargs = _base_kwargs()
    kwargs["amount"] = Decimal("-100.00")
    with pytest.raises(ValidationError, match="non-negative"):
        CanonicalTransaction(**kwargs)


def test_amount_minor_mismatch_is_rejected() -> None:
    kwargs = _base_kwargs()
    kwargs["amount_minor"] = 999
    with pytest.raises(ValidationError, match="inconsistent"):
        CanonicalTransaction(**kwargs)


def test_currency_exponent_mismatch_is_rejected() -> None:
    kwargs = _base_kwargs()
    kwargs["currency_exponent_value"] = 3  # INR is 2, not 3
    with pytest.raises(ValidationError, match="does not match"):
        CanonicalTransaction(**kwargs)


def test_reverses_reference_requires_is_reversal() -> None:
    """AE-12: reverses_reference set without is_reversal=True is inconsistent."""
    kwargs = _base_kwargs()
    kwargs["reverses_reference"] = "HDFC-REF-000100"
    kwargs["is_reversal"] = False
    with pytest.raises(ValidationError, match="reverses_reference set"):
        CanonicalTransaction(**kwargs)


def test_reversal_with_reference_is_valid() -> None:
    kwargs = _base_kwargs()
    kwargs["is_reversal"] = True
    kwargs["reverses_reference"] = "HDFC-REF-000100"
    txn = CanonicalTransaction(**kwargs)
    assert txn.is_reversal is True
    assert txn.reverses_reference == "HDFC-REF-000100"


def test_settlement_date_is_independent_of_txn_date() -> None:
    """AE-02: settlement_date is a distinct, optional, per-transaction field
    — not derived from a statement-level balance element."""
    kwargs = _base_kwargs()
    kwargs["settlement_date"] = date(2026, 3, 17)
    txn = CanonicalTransaction(**kwargs)
    assert txn.txn_date == date(2026, 3, 15)
    assert txn.settlement_date == date(2026, 3, 17)


def test_currency_is_uppercased() -> None:
    kwargs = _base_kwargs()
    kwargs["currency"] = "inr"
    txn = CanonicalTransaction(**kwargs)
    assert txn.currency == "INR"


def test_model_is_frozen() -> None:
    txn = CanonicalTransaction(**_base_kwargs())
    with pytest.raises(ValidationError):
        txn.amount = Decimal("1.00")  # type: ignore[misc]


def test_unknown_currency_is_rejected_at_construction() -> None:
    kwargs = _base_kwargs()
    kwargs["currency"] = "ZZZ"
    kwargs["currency_exponent_value"] = 2
    with pytest.raises(ValidationError):
        CanonicalTransaction(**kwargs)
