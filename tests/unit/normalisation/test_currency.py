# tests/unit/normalisation/test_currency.py
"""Tests for recon.normalisation.currency."""

from __future__ import annotations

import pytest

from recon.domain.enums import CurrencySource
from recon.normalisation.currency import (
    CurrencyNormalisationError,
    normalise_currency,
    resolve_currency,
)


def test_lowercase_currency_is_uppercased() -> None:
    assert normalise_currency("inr") == "INR"


def test_whitespace_is_stripped() -> None:
    assert normalise_currency("  INR  ") == "INR"


def test_unknown_currency_raises() -> None:
    with pytest.raises(CurrencyNormalisationError, match="no minor-unit exponent"):
        normalise_currency("ZZZ")


def test_wrong_length_code_raises() -> None:
    with pytest.raises(CurrencyNormalisationError, match="3-letter"):
        normalise_currency("EURO")


def test_non_alphabetic_code_raises() -> None:
    with pytest.raises(CurrencyNormalisationError, match="3-letter"):
        normalise_currency("IN1")


def test_resolve_currency_prefers_entry_level_when_present() -> None:
    code, source = resolve_currency("usd", None, "INR")
    assert code == "USD"
    assert source == CurrencySource.ENTRY_LEVEL


def test_resolve_currency_falls_back_to_statement_level() -> None:
    code, source = resolve_currency(None, "gbp", "INR")
    assert code == "GBP"
    assert source == CurrencySource.STATEMENT_LEVEL


def test_resolve_currency_falls_back_to_bank_config_default() -> None:
    code, source = resolve_currency(None, None, "inr")
    assert code == "INR"
    assert source == CurrencySource.BANK_CONFIG_DEFAULT


def test_resolve_currency_treats_blank_string_as_absent() -> None:
    """An empty/whitespace-only entry-level value must fall through to
    statement-level, not be treated as a present-but-empty currency."""
    code, source = resolve_currency("   ", "gbp", "INR")
    assert code == "GBP"
    assert source == CurrencySource.STATEMENT_LEVEL
