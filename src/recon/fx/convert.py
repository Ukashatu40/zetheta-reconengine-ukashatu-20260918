# src/recon/fx/convert.py
"""Currency conversion on integer minor units (A5.1, AE-04).

Rates are INR per one unit of a foreign currency, held as Decimal. Every
conversion rounds exactly once, with an explicit mode, and returns an integer
count of minor units; nothing here touches float.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from recon.domain.money import currency_exponent

_INR = "INR"
_HUNDRED = Decimal(100)


def to_inr_minor(amount_minor: int, currency: str, rate_inr: Decimal) -> int:
    """Converts minor units of `currency` to INR paise at rate_inr INR per unit."""
    if currency == _INR:
        return amount_minor
    scale = Decimal(10) ** (currency_exponent(_INR) - currency_exponent(currency))
    return int((Decimal(amount_minor) * rate_inr * scale).to_integral_value(ROUND_HALF_EVEN))


def from_inr_minor(inr_minor: int, currency: str, rate_inr: Decimal) -> int:
    """Converts INR paise to minor units of `currency`."""
    if currency == _INR:
        return inr_minor
    scale = Decimal(10) ** (currency_exponent(currency) - currency_exponent(_INR))
    return int((Decimal(inr_minor) / rate_inr * scale).to_integral_value(ROUND_HALF_EVEN))


def relative_difference_percent(a_minor: int, b_minor: int) -> Decimal:
    """|a - b| / mean(a, b) as a percentage (A3.3's cross-currency tolerance
    test). Both amounts must be in the same currency. Zero when both are zero."""
    mean = Decimal(a_minor + b_minor) / 2
    if mean == 0:
        return Decimal(0)
    return abs(Decimal(a_minor - b_minor)) / mean * _HUNDRED


def implied_rate(inr_minor: int, foreign_minor: int, currency: str) -> Decimal | None:
    """The INR-per-unit rate implied by an INR amount and a foreign amount
    (B4.2's bank-quoted netting rate). None when the foreign amount is zero."""
    if foreign_minor == 0:
        return None
    scale = Decimal(10) ** (currency_exponent(currency) - currency_exponent(_INR))
    return Decimal(inr_minor) / (Decimal(foreign_minor) * scale)
