# src/recon/normalisation/amounts.py
"""Amount standardisation (R41, D2): converts a parser's raw amount text
into a Money value, applying bank-configured decimal/thousands
separators and a single, explicit, configured rounding mode.

This is the direct application of recon.domain.money.Money to real parsed
text — WP1 built Money as a pure value object with no I/O; this module is
the boundary where untrusted source text actually becomes one.

AE for the NPCI/float-drift lesson (Case Study C3, see
docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md): normalise_amount never touches
Python float at any point in its execution path. Decimal is constructed
directly from a cleaned string.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from recon.domain.money import DEFAULT_ROUNDING, Money, UnknownCurrencyError


class AmountNormalisationError(ValueError):
    """Raised when the amount text cannot be interpreted as a decimal
    number at all, or when the resulting currency has no registered
    minor-unit exponent (see recon.domain.money.UnknownCurrencyError)."""


def normalise_amount(
    amount_text: str,
    currency: str,
    *,
    decimal_separator: str = ".",
    thousands_separator: str | None = None,
    rounding: str = DEFAULT_ROUNDING,
) -> Money:
    cleaned = amount_text.strip()
    if thousands_separator:
        cleaned = cleaned.replace(thousands_separator, "")
    if decimal_separator != ".":
        cleaned = cleaned.replace(decimal_separator, ".")

    try:
        decimal_value = Decimal(cleaned)
    except InvalidOperation as exc:
        raise AmountNormalisationError(
            f"{amount_text!r} could not be parsed as a decimal amount"
        ) from exc

    try:
        return Money.from_decimal(decimal_value, currency, rounding=rounding)
    except UnknownCurrencyError as exc:
        raise AmountNormalisationError(str(exc)) from exc


def sign_for_direction(direction: str) -> int:
    """Returns +1 for a credit-direction mark, -1 for debit — used by
    callers that need a signed amount for arithmetic (e.g. summing a
    running balance), while Money itself always stores a non-negative
    magnitude with direction carried separately (AE: sign belongs on
    direction, never on the amount — see
    CanonicalTransaction._amount_non_negative's validator in WP1).

    Recognises MT940's full mark set (C, D, RC, RD) and CAMT.053's
    CRDT/DBIT — the two vocabularies this codebase's parsers actually
    produce. CSV's direction text is bank-config-mapped and not
    standardised at the parsing stage, so it is deliberately NOT accepted
    here; a CSV bank's direction column must be mapped to one of these
    canonical marks during that bank's own normalisation step before
    reaching this function.
    """
    normalised = direction.strip().upper()
    if normalised in ("C", "RC", "CRDT"):
        return 1
    if normalised in ("D", "RD", "DBIT"):
        return -1
    raise ValueError(f"{direction!r} is not a recognised direction mark")
