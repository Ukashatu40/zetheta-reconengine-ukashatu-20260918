# src/recon/ingestion/balances.py
"""Balance-level reconciliation (Case Study C5, "The Frozen Funds").

Transaction-level matching can pass while the money is not there. Every MT940
and CAMT.053 statement states an opening and a closing balance, so the entries
must satisfy

    opening + sum(signed entries) == closing.

A statement that fails this while its transactions all matched is the critical
signal C5 describes. Entry signs follow the bank's own marks: credit and
reversal of a debit add; debit and reversal of a credit subtract. (A reversal of
a credit takes money out again, so it is a debit for balance arithmetic even
though the canonical model keeps it as direction CR with is_reversal set, AE-12.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from recon.domain.money import UnknownCurrencyError, currency_exponent
from recon.ingestion.parsed_row import ParsedRow
from recon.ingestion.parsers.camt053.parser import CAMT053StatementHeader
from recon.ingestion.parsers.mt940.state_machine import MT940StatementHeader

_SIGN = {"C": 1, "CRDT": 1, "RD": 1, "D": -1, "DBIT": -1, "RC": -1}
_CAMT_OPENING = ("OPBD", "PRCD")
_CAMT_CLOSING = ("CLBD",)


@dataclass(frozen=True, slots=True)
class StatementBalances:
    statement_ref: str | None
    account: str | None
    currency: str
    opening_minor: int
    closing_minor: int
    opening_date: date | None
    closing_date: date | None


@dataclass(frozen=True, slots=True)
class BalanceCheck:
    balances: StatementBalances
    entry_count: int
    unparseable_entries: int
    net_entries_minor: int

    @property
    def computed_closing_minor(self) -> int:
        return self.balances.opening_minor + self.net_entries_minor

    @property
    def difference_minor(self) -> int:
        """Stated closing minus computed closing; zero when the statement balances."""
        return self.balances.closing_minor - self.computed_closing_minor

    @property
    def balanced(self) -> bool:
        return self.difference_minor == 0


def _to_minor(amount_raw: str | None, currency: str, decimal_separator: str = ".") -> int | None:
    if not amount_raw:
        return None
    try:
        value = Decimal(amount_raw.strip().replace(decimal_separator, "."))
        exponent = currency_exponent(currency)
    except (InvalidOperation, UnknownCurrencyError):
        return None
    return int(value.scaleb(exponent).to_integral_value())


def _parse_date(raw: str | None, fmt: str, width: int) -> date | None:
    """A date from the first `width` characters (a CAMT date may carry a time)."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip()[:width], fmt).date()  # noqa: DTZ007
    except ValueError:
        return None


def mt940_balances(
    header: MT940StatementHeader, decimal_separator: str
) -> StatementBalances | None:
    opening, closing = header.opening_balance, header.closing_balance
    if opening is None or closing is None:
        return None
    o = _to_minor(opening.amount_raw, opening.currency, decimal_separator)
    c = _to_minor(closing.amount_raw, closing.currency, decimal_separator)
    if o is None or c is None:
        return None
    return StatementBalances(
        statement_ref=header.statement_number or header.transaction_reference,
        account=header.account_id,
        currency=opening.currency,
        opening_minor=o * _SIGN.get(opening.mark, 1),
        closing_minor=c * _SIGN.get(closing.mark, 1),
        opening_date=_parse_date(opening.date_raw, "%y%m%d", 6),
        closing_date=_parse_date(closing.date_raw, "%y%m%d", 6),
    )


def camt_balances(header: CAMT053StatementHeader) -> StatementBalances | None:
    opening = next((b for b in header.balances if b.type_code in _CAMT_OPENING), None)
    closing = next((b for b in header.balances if b.type_code in _CAMT_CLOSING), None)
    if opening is None or closing is None or not opening.currency or not closing.currency:
        return None
    o = _to_minor(opening.amount_raw, opening.currency)
    c = _to_minor(closing.amount_raw, closing.currency)
    if o is None or c is None:
        return None
    return StatementBalances(
        statement_ref=header.statement_id or header.message_id,
        account=header.account_id,
        currency=opening.currency,
        opening_minor=o * _SIGN.get(opening.credit_debit_indicator, 1),
        closing_minor=c * _SIGN.get(closing.credit_debit_indicator, 1),
        opening_date=_parse_date(opening.date_raw, "%Y-%m-%d", 10),
        closing_date=_parse_date(closing.date_raw, "%Y-%m-%d", 10),
    )


def check_statement(
    balances: StatementBalances, rows: list[ParsedRow], decimal_separator: str = "."
) -> BalanceCheck:
    """Sums the signed entries of a statement. A row whose amount or mark cannot be
    read is counted as unparseable rather than silently ignored, so a break caused
    by a bad row is visible as such."""
    net = 0
    entries = 0
    unparseable = 0
    for row in rows:
        mark = row.mapped.get("direction", "").strip().upper()
        currency = row.mapped.get("currency") or balances.currency
        amount = _to_minor(row.mapped.get("amount"), currency, decimal_separator)
        if mark not in _SIGN or amount is None:
            unparseable += 1
            continue
        entries += 1
        net += amount * _SIGN[mark]
    return BalanceCheck(balances, entries, unparseable, net)
