# src/recon/samples/entries.py
"""Synthetic statement entries and writers for each format the engine ingests:
CSV in a bank's own column layout, MT940 and CAMT.053.

Everything here is invented and deterministic: no real account, name or card
number appears (DPDPA 2023, A8.1). The writers produce files the real parsers
read back, so a sample file exercises the same code a bank's file would.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from recon.config.models import BankConfig
from recon.domain.money import currency_exponent

_MT940_DEFAULT_OPENING_MINOR = 100_000_000  # INR 10,00,000.00 before the day's entries


@dataclass(frozen=True, slots=True)
class Entry:
    """One line of a statement or ledger."""

    ref: str
    day: date
    amount_minor: int
    direction: str  # CR or DR
    counterparty: str | None = None
    narration: str = ""
    currency: str = "INR"
    time: str | None = None  # "HH:MM", for formats that carry a time of day
    settle_day: date | None = None  # CAMT ValDt when it differs from the booking date
    reversal: bool = False


def decimal_text(amount_minor: int, currency: str, separator: str = ".") -> str:
    exponent = currency_exponent(currency)
    value = Decimal(amount_minor).scaleb(-exponent)
    text = f"{value:.{exponent}f}"
    return text.replace(".", separator) if separator != "." else text


# -- CSV ------------------------------------------------------------------------------


def write_csv(
    config: BankConfig, entries: Sequence[Entry], *, corrupt_lines: Sequence[int] = ()
) -> str:
    """A CSV in the bank's own column layout. `corrupt_lines` (1-based data rows) get an
    unparseable date, to plant FORMAT_ERROR rows."""
    assert config.csv is not None
    csv_config = config.csv
    mapping = csv_config.column_mapping
    fields = ["reference", "txn_date", "amount", "direction", "counterparty_name", "narration"]
    header = [mapping[f] for f in fields if f in mapping]
    used = [f for f in fields if f in mapping]
    out = io.StringIO()
    writer = csv.writer(out, delimiter=csv_config.delimiter, lineterminator="\n")
    writer.writerow(header)
    for line_no, e in enumerate(entries, start=1):
        if line_no in corrupt_lines:
            day_text = "31-31-2026"
        else:
            hour_minute = (e.time or "00:00").split(":")
            stamp = _format_day(
                e.day, csv_config.date_format, int(hour_minute[0]), int(hour_minute[1])
            )
            day_text = stamp
        values = {
            "reference": e.ref,
            "txn_date": day_text,
            "amount": _csv_amount(e.amount_minor, config),
            "direction": e.direction,
            "counterparty_name": e.counterparty or "",
            "narration": e.narration,
        }
        writer.writerow([values[f] for f in used])
    return out.getvalue()


def _format_day(day: date, fmt: str, hour: int, minute: int) -> str:
    return (
        fmt.replace("%d", f"{day.day:02d}")
        .replace("%m", f"{day.month:02d}")
        .replace("%Y", f"{day.year:04d}")
        .replace("%H", f"{hour:02d}")
        .replace("%M", f"{minute:02d}")
    )


def _csv_amount(amount_minor: int, config: BankConfig) -> str:
    assert config.csv is not None
    text = decimal_text(amount_minor, "INR", config.csv.amount_decimal_separator)
    if config.csv.amount_thousands_separator:
        whole, _, fraction = text.partition(config.csv.amount_decimal_separator)
        grouped = f"{int(whole):,}".replace(",", config.csv.amount_thousands_separator)
        text = grouped + config.csv.amount_decimal_separator + fraction
    return text


# -- MT940 -----------------------------------------------------------------------------


def write_mt940(
    entries: Sequence[Entry],
    *,
    decimal_separator: str,
    account: str,
    statement_no: str,
    opening_minor: int = _MT940_DEFAULT_OPENING_MINOR,
    unbalanced_by_minor: int = 0,
) -> str:
    """One MT940 statement. The closing balance is opening plus the signed entries, so the
    statement balances unless `unbalanced_by_minor` deliberately breaks it (Case Study C5)."""
    net = sum(_signed(e) for e in entries)
    closing = opening_minor + net + unbalanced_by_minor
    first_day = min((e.day for e in entries), default=date(2026, 1, 1))
    last_day = max((e.day for e in entries), default=first_day)
    lines = [
        f":20:STMT{statement_no.replace('/', '')}",
        f":25:{account}",
        f":28C:{statement_no}",
        f":60F:{_balance_mark(opening_minor)}{first_day:%y%m%d}INR"
        f"{decimal_text(abs(opening_minor), 'INR', decimal_separator)}",
    ]
    for e in entries:
        mark = (
            ("RD" if e.direction == "DR" else "RC")
            if e.reversal
            else ("D" if e.direction == "DR" else "C")
        )
        amount = decimal_text(e.amount_minor, e.currency, decimal_separator)
        lines.append(f":61:{e.day:%y%m%d}{e.day:%m%d}{mark}{amount}NTRF{e.ref}")
        lines.append(f":86:{e.narration or 'Payment'}")
    lines.append(
        f":62F:{_balance_mark(closing)}{last_day:%y%m%d}INR"
        f"{decimal_text(abs(closing), 'INR', decimal_separator)}"
    )
    return "\n".join(lines) + "\n"


def _balance_mark(minor: int) -> str:
    return "D" if minor < 0 else "C"


def _signed(e: Entry) -> int:
    """Balance effect: a credit adds, a debit subtracts; a reversal does the opposite."""
    sign = 1 if e.direction == "CR" else -1
    return -sign * e.amount_minor if e.reversal else sign * e.amount_minor


# -- CAMT.053 ----------------------------------------------------------------------------


_CAMT_NS = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"


def write_camt(
    entries: Sequence[Entry],
    *,
    currency: str,
    account: str,
    statement_id: str,
    opening_minor: int = _MT940_DEFAULT_OPENING_MINOR,
) -> str:
    """One CAMT.053 statement in a single currency, with the deep NtryDtls/TxDtls nesting
    the D10 Kotak sample describes."""
    net = sum(_signed(e) for e in entries)
    closing = opening_minor + net
    first_day = min((e.day for e in entries), default=date(2026, 1, 1))
    last_day = max((e.day for e in entries), default=first_day)

    def balance(code: str, minor: int, day: date) -> str:
        indicator = "DBIT" if minor < 0 else "CRDT"
        return (
            f"<Bal><Tp><CdOrPrtry><Cd>{code}</Cd></CdOrPrtry></Tp>"
            f'<Amt Ccy="{currency}">{decimal_text(abs(minor), currency)}</Amt>'
            f"<CdtDbtInd>{indicator}</CdtDbtInd><Dt><Dt>{day.isoformat()}</Dt></Dt></Bal>"
        )

    body = [
        f'<?xml version="1.0" encoding="UTF-8"?>\n<BkToCstmrStmt xmlns="{_CAMT_NS}">',
        f"<GrpHdr><MsgId>MSG-{escape(statement_id)}</MsgId></GrpHdr>",
        f"<Stmt><Id>{escape(statement_id)}</Id><Acct><Id><IBAN>{escape(account)}</IBAN></Id></Acct>",
        balance("OPBD", opening_minor, first_day),
        balance("CLBD", closing, last_day),
    ]
    for e in entries:
        indicator = "DBIT" if e.direction == "DR" else "CRDT"
        settle = e.settle_day or e.day
        party = (
            f"<RltdPties><Cdtr><Nm>{escape(e.counterparty)}</Nm></Cdtr></RltdPties>"
            if e.counterparty
            else ""
        )
        body.append(
            f'<Ntry><Amt Ccy="{e.currency}">{decimal_text(e.amount_minor, e.currency)}</Amt>'
            f"<CdtDbtInd>{indicator}</CdtDbtInd><Sts>BOOK</Sts>"
            f"<BookgDt><Dt>{e.day.isoformat()}</Dt></BookgDt><ValDt><Dt>{settle.isoformat()}</Dt></ValDt>"
            f"<NtryDtls><TxDtls><Refs><EndToEndId>{escape(e.ref)}</EndToEndId></Refs>{party}"
            f"<AddtlTxInf>{escape(e.narration or 'Payment')}</AddtlTxInf>"
            "</TxDtls></NtryDtls></Ntry>"
        )
    body.append("</Stmt></BkToCstmrStmt>")
    return "\n".join(body) + "\n"
