# tests/unit/normalisation/test_reference.py
"""Tests for recon.normalisation.reference."""

from __future__ import annotations

from recon.normalisation.reference import clean_reference, pad_reference


def test_whitespace_is_stripped() -> None:
    assert clean_reference("  REF001  ") == "REF001"


def test_hyphens_underscores_periods_removed() -> None:
    assert clean_reference("REF-001_ABC.123") == "REF001ABC123"


def test_internal_whitespace_removed() -> None:
    assert clean_reference("REF 001 ABC") == "REF001ABC"


def test_uppercased() -> None:
    assert clean_reference("ref001abc") == "REF001ABC"


def test_double_slash_bank_reference_separator_is_preserved() -> None:
    """MT940's REF.../BANKREF convention (AE-03's extended-reference
    deviation) must survive cleaning intact — it's structurally
    meaningful, not formatting noise."""
    assert clean_reference("REF0001234567//BANKREF001") == "REF0001234567//BANKREF001"


def test_single_slash_is_not_treated_as_the_double_slash_convention() -> None:
    # a lone "/" is left as-is; only the "//" pair is specially preserved
    assert clean_reference("REF/001") == "REF/001"


def test_no_truncation_by_default() -> None:
    long_ref = "REF" + "9" * 50
    assert clean_reference(long_ref) == long_ref


def test_truncation_only_when_explicitly_requested() -> None:
    long_ref = "REF" + "9" * 50
    result = clean_reference(long_ref, max_length=10)
    assert len(result) == 10
    assert result == long_ref[:10]


def test_pad_reference_left_pads_with_default_zero() -> None:
    assert pad_reference("42", 6) == "000042"


def test_pad_reference_does_not_truncate_longer_input() -> None:
    assert pad_reference("1234567", 6) == "1234567"


def test_pad_reference_custom_pad_char() -> None:
    assert pad_reference("42", 6, pad_char="X") == "XXXX42"
