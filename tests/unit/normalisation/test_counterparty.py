# tests/unit/normalisation/test_counterparty.py
"""Tests for recon.normalisation.counterparty."""

from __future__ import annotations

from recon.normalisation.counterparty import clean_counterparty_name, register_abbreviation


def test_uppercased() -> None:
    assert clean_counterparty_name("acme corp") == "ACME CORPORATION"


def test_pvt_ltd_expansion() -> None:
    assert clean_counterparty_name("Beta Pvt Ltd") == "BETA PRIVATE LIMITED"


def test_special_characters_stripped() -> None:
    assert clean_counterparty_name("Acme & Sons, Inc.") == "ACME SONS INCORPORATED"


def test_multiple_whitespace_collapsed() -> None:
    assert clean_counterparty_name("Acme    Corp") == "ACME CORPORATION"


def test_leading_trailing_whitespace_stripped() -> None:
    assert clean_counterparty_name("  Acme Corp  ") == "ACME CORPORATION"


def test_unicode_characters_are_preserved_not_stripped() -> None:
    """Special-character stripping targets PUNCTUATION, not
    non-ASCII/Unicode letters — a name legitimately containing accented
    or non-Latin characters must not be mangled."""
    assert clean_counterparty_name("Café Société") == "CAFÉ SOCIÉTÉ"


def test_unknown_abbreviations_pass_through_unchanged() -> None:
    assert clean_counterparty_name("Acme Solutions") == "ACME SOLUTIONS"


def test_register_abbreviation_extends_the_table() -> None:
    register_abbreviation("assoc", "association")
    assert clean_counterparty_name("Beta Assoc") == "BETA ASSOCIATION"


def test_hyphenated_name_special_char_stripped() -> None:
    assert clean_counterparty_name("Smith-Jones Trading") == "SMITH JONES TRADING"
