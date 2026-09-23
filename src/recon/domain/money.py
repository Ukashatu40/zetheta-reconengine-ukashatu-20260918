# src/recon/domain/money.py
"""Money value object: Decimal arithmetic, integer minor units, explicit
rounding.

Corrects two errors identified in docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md:

AE-04 (NPCI rounding attribution / float-drift conflation): all arithmetic
here runs on Decimal or on integer minor units — never on float — and the
rounding mode is an explicit, configured parameter, never the ambient
decimal.Context.

AE-17 (DECIMAL(18,4) vs per-currency precision): amounts are stored both as
Decimal at 4dp (matching the PDF's schema literally) and as an integer
`minor_units` value scaled by the currency's actual ISO 4217 exponent (2 for
INR/USD/GBP, 0 for JPY, 3 for KWD/BHD). All comparison, summation and
subset-sum matching use `minor_units`, which is exact integer arithmetic and
side-steps Decimal-scale mismatches entirely.

No code outside this module should call Python's built-in round() on a money
value, or read decimal.getcontext() — see the banned-api rule in
pyproject.toml's ruff config.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

# ISO 4217 minor-unit exponents. Not exhaustive — extended as new currencies
# appear in bank configs. Absence from this table is a configuration error,
# not silently assumed to be 2.
_CURRENCY_EXPONENTS: dict[str, int] = {
    "INR": 2,
    "USD": 2,
    "GBP": 2,
    "EUR": 2,
    "SGD": 2,
    "AED": 2,
    "JPY": 0,
    "KWD": 3,
    "BHD": 3,
    "OMR": 3,
}

DEFAULT_ROUNDING = ROUND_HALF_EVEN


class UnknownCurrencyError(ValueError):
    """Raised when a currency code has no known minor-unit exponent.

    Deliberately not defaulted to 2 — a silent default is exactly the kind
    of assumption AE-17 exists to eliminate. Callers must register the
    currency's exponent explicitly via `register_currency_exponent`.
    """


def register_currency_exponent(currency: str, exponent: int) -> None:
    """Register or override a currency's minor-unit exponent.

    Exists so bank configuration (config/banks/*.yaml) can extend currency
    support at startup without editing this module.
    """
    if exponent < 0:
        raise ValueError(f"exponent must be >= 0, got {exponent}")
    _CURRENCY_EXPONENTS[currency.upper()] = exponent


def currency_exponent(currency: str) -> int:
    """Look up a currency's minor-unit exponent, raising if unknown."""
    try:
        return _CURRENCY_EXPONENTS[currency.upper()]
    except KeyError:
        raise UnknownCurrencyError(
            f"no minor-unit exponent registered for currency {currency!r}; "
            "call register_currency_exponent() before using it"
        ) from None


@dataclass(frozen=True, slots=True)
class Money:
    """An amount in a specific currency, exact to the currency's minor unit.

    `amount` is the Decimal value in major units (e.g. rupees), always
    quantized to the currency's exponent at construction time using the
    supplied rounding mode. `minor_units` is the same value as an integer
    count of minor units (paise, cents, fils) and is what all matching
    arithmetic (subset-sum, tolerance comparison) should use.

    Construction always rounds explicitly; there is no implicit rounding
    path. This is intentional — AE-04's lesson is that rounding must be a
    visible, configured decision, not something that happens by accident of
    which arithmetic operation ran.
    """

    amount: Decimal
    currency: str
    minor_units: int

    @classmethod
    def from_decimal(
        cls,
        amount: Decimal | str | int,
        currency: str,
        *,
        rounding: str = DEFAULT_ROUNDING,
    ) -> Money:
        currency = currency.upper()
        exponent = currency_exponent(currency)
        quantum = Decimal(1).scaleb(-exponent)

        try:
            decimal_amount = Decimal(amount) if not isinstance(amount, Decimal) else amount
        except InvalidOperation as exc:
            raise ValueError(f"cannot interpret {amount!r} as a Decimal amount") from exc

        quantized = decimal_amount.quantize(quantum, rounding=rounding)
        minor_units = int(quantized.scaleb(exponent))
        return cls(amount=quantized, currency=currency, minor_units=minor_units)

    @classmethod
    def from_minor_units(cls, minor_units: int, currency: str) -> Money:
        """Construct from an exact minor-unit integer.

        No rounding occurs here — the input is already exact, which is the
        expected path when reconstructing a Money from a database row where
        `amount_minor BIGINT` was the persisted source of truth.
        """
        currency = currency.upper()
        exponent = currency_exponent(currency)
        amount = Decimal(minor_units).scaleb(-exponent)
        return cls(amount=amount, currency=currency, minor_units=minor_units)

    def __post_init__(self) -> None:
        exponent = currency_exponent(self.currency)
        expected_minor = int(self.amount.scaleb(exponent))
        if expected_minor != self.minor_units:
            raise ValueError(
                f"amount {self.amount} and minor_units {self.minor_units} "
                f"are inconsistent for currency {self.currency} "
                f"(exponent {exponent}); expected minor_units={expected_minor}"
            )

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"cannot combine {self.currency} and {other.currency} directly; "
                "convert via recon.fx before combining amounts in different currencies"
            )

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money.from_minor_units(self.minor_units + other.minor_units, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money.from_minor_units(self.minor_units - other.minor_units, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.minor_units < other.minor_units

    def __le__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.minor_units <= other.minor_units

    def is_zero(self) -> bool:
        return self.minor_units == 0

    def abs_diff(self, other: Money) -> Money:
        """Absolute difference, used for amount-tolerance rule evaluation."""
        self._require_same_currency(other)
        return Money.from_minor_units(abs(self.minor_units - other.minor_units), self.currency)

    def __repr__(self) -> str:
        return f"Money({self.amount} {self.currency})"


def sum_money(amounts: list[Money], currency: str) -> Money:
    """Sum a list of same-currency Money values on integer minor units.

    Used by split/netted transaction matching (recon.matching.strategies)
    where correctness of the sum is a financial-safety property, not a
    convenience — see AE-04's explicit demonstration that Decimal
    intermediate summation of many values can still drift if any step
    re-enters float, which this function structurally cannot do.
    """
    total_minor = 0
    for amount in amounts:
        if amount.currency.upper() != currency.upper():
            raise ValueError(
                f"sum_money requires uniform currency; got {amount.currency} "
                f"mixed with target {currency}"
            )
        total_minor += amount.minor_units
    return Money.from_minor_units(total_minor, currency)
