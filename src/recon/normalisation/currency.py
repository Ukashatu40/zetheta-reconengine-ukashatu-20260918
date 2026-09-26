# src/recon/normalisation/currency.py
"""Currency normalisation (R39): validates a currency code against ISO
4217 (via recon.domain.money's registered exponent table, which IS the
set of currencies this system actually knows how to handle), and records
whether the value came from an entry-level or statement-level source
(AE-... — see docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md's currency-source
ambiguity entry, addressed already in CanonicalTransaction.currency_source
at the domain-model level in WP1; this module is what actually determines
which source a given normalisation call used).
"""

from __future__ import annotations

from recon.domain.enums import CurrencySource
from recon.domain.money import UnknownCurrencyError, currency_exponent


class CurrencyNormalisationError(ValueError):
    """Raised when a currency code is not a recognised, 3-letter code with
    a registered minor-unit exponent."""


ISO_CURRENCY_CODE_LENGTH = 3


def normalise_currency(raw_currency: str) -> str:
    """Uppercases and validates a currency code. Raises if the resulting
    code has no registered minor-unit exponent — an unrecognised currency
    should surface as an explicit error at normalisation time, not
    propagate silently into matching where it would fail more confusingly
    later."""
    candidate = raw_currency.strip().upper()
    if len(candidate) != ISO_CURRENCY_CODE_LENGTH or not candidate.isalpha():
        raise CurrencyNormalisationError(f"{raw_currency!r} is not a 3-letter currency code")

    try:
        currency_exponent(candidate)  # raises UnknownCurrencyError if unregistered
    except UnknownCurrencyError as exc:
        raise CurrencyNormalisationError(str(exc)) from exc

    return candidate


def resolve_currency(
    entry_level_currency: str | None,
    statement_level_currency: str | None,
    bank_config_default: str,
) -> tuple[str, CurrencySource]:
    """Resolution order per AE (multi-currency MT940 statements, D2):
    (1) an entry-level currency, when the source actually provides one,
    (2) the statement-level default, when the entry doesn't,
    (3) the bank's configured default currency as a last resort.

    Returns both the resolved code and which source it came from, so the
    caller (normalisation pipeline, Increment 3) can populate
    CanonicalTransaction.currency_source accurately rather than guessing.
    """
    if entry_level_currency and entry_level_currency.strip():
        return normalise_currency(entry_level_currency), CurrencySource.ENTRY_LEVEL

    if statement_level_currency and statement_level_currency.strip():
        return normalise_currency(statement_level_currency), CurrencySource.STATEMENT_LEVEL

    return normalise_currency(bank_config_default), CurrencySource.BANK_CONFIG_DEFAULT
