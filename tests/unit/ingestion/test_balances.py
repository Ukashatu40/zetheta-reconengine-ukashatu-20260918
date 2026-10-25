"""Balance-level reconciliation arithmetic (Case Study C5)."""

from __future__ import annotations

from datetime import date

from recon.ingestion.balances import (
    camt_balances,
    check_statement,
    mt940_balances,
)
from recon.ingestion.parsed_row import ParsedRow
from recon.ingestion.parsers.camt053.parser import CAMT053Balance, CAMT053StatementHeader
from recon.ingestion.parsers.mt940.state_machine import MT940Balance, MT940StatementHeader


def _mt940_header(opening: str = "1000,00", closing: str = "1250,00") -> MT940StatementHeader:
    return MT940StatementHeader(
        transaction_reference="REF20",
        account_id="12345678",
        statement_number="00001/001",
        opening_balance=MT940Balance("60F", "C", "260315", "INR", opening),
        closing_balance=MT940Balance("62F", "C", "260316", "INR", closing),
    )


def _row(mark: str, amount: str, currency: str = "") -> ParsedRow:
    mapped = {"direction": mark, "amount": amount}
    if currency:
        mapped["currency"] = currency
    return ParsedRow(line_no=1, raw_fields={}, mapped=mapped)


def test_mt940_balances_are_converted_to_signed_minor_units() -> None:
    balances = mt940_balances(_mt940_header(), ",")
    assert balances is not None
    assert (balances.opening_minor, balances.closing_minor, balances.currency) == (
        100_000,
        125_000,
        "INR",
    )
    assert (balances.opening_date, balances.closing_date) == (date(2026, 3, 15), date(2026, 3, 16))
    assert (balances.account, balances.statement_ref) == ("12345678", "00001/001")


def test_a_debit_balance_is_negative() -> None:
    header = MT940StatementHeader(
        None, None, None,
        MT940Balance("60F", "D", "260315", "INR", "50,00"),
        MT940Balance("62F", "D", "260316", "INR", "20,00"),
    )  # fmt: skip
    balances = mt940_balances(header, ",")
    assert balances is not None
    assert (balances.opening_minor, balances.closing_minor) == (-5_000, -2_000)


def test_a_period_decimal_separator_is_honoured() -> None:
    balances = mt940_balances(_mt940_header("1000.00", "1250.00"), ".")
    assert balances is not None and balances.closing_minor == 125_000


def test_missing_or_unreadable_balances_give_none() -> None:
    assert mt940_balances(MT940StatementHeader(None, None, None, None, None), ",") is None
    assert mt940_balances(_mt940_header("abc", "1,00"), ",") is None


def test_a_statement_that_adds_up_balances() -> None:
    balances = mt940_balances(_mt940_header(), ",")
    assert balances is not None
    rows = [_row("C", "300,00"), _row("D", "50,00")]  # +300 -50 = +250
    check = check_statement(balances, rows, ",")
    assert (check.net_entries_minor, check.computed_closing_minor, check.difference_minor) == (
        25_000,
        125_000,
        0,
    )
    assert check.balanced is True and (check.entry_count, check.unparseable_entries) == (2, 0)


def test_a_missing_entry_is_a_break_with_the_size_of_the_gap() -> None:
    balances = mt940_balances(_mt940_header(), ",")
    assert balances is not None
    check = check_statement(
        balances, [_row("C", "300,00")], ","
    )  # the 50.00 debit is not in the file
    assert check.balanced is False and check.difference_minor == -5_000


def test_reversal_marks_move_the_balance_the_opposite_way() -> None:
    balances = mt940_balances(_mt940_header("1000,00", "1100,00"), ",")
    assert balances is not None
    # RD (reversal of a debit) adds 150; RC (reversal of a credit) takes 50 out again
    check = check_statement(balances, [_row("RD", "150,00"), _row("RC", "50,00")], ",")
    assert check.net_entries_minor == 10_000 and check.balanced is True


def test_an_unreadable_row_is_counted_not_hidden() -> None:
    balances = mt940_balances(_mt940_header(), ",")
    assert balances is not None
    check = check_statement(
        balances, [_row("C", "300,00"), _row("D", "oops"), _row("X", "1,00")], ","
    )
    assert (check.entry_count, check.unparseable_entries) == (1, 2)
    assert check.balanced is False


def _camt_header(*balances: CAMT053Balance) -> CAMT053StatementHeader:
    return CAMT053StatementHeader("MSG1", "IBAN123", "STMT9", list(balances))


def test_camt_uses_the_opening_and_closing_booked_balances() -> None:
    header = _camt_header(
        CAMT053Balance("OPBD", "CRDT", "2026-03-15", "INR", "1000.00"),
        CAMT053Balance("CLAV", "CRDT", "2026-03-16", "INR", "9999.00"),  # available: ignored
        CAMT053Balance("CLBD", "CRDT", "2026-03-16", "INR", "1250.00"),
    )
    balances = camt_balances(header)
    assert balances is not None
    assert (balances.opening_minor, balances.closing_minor) == (100_000, 125_000)
    assert balances.statement_ref == "STMT9" and balances.account == "IBAN123"
    check = check_statement(balances, [_row("CRDT", "300.00"), _row("DBIT", "50.00")])
    assert check.balanced is True


def test_a_previously_closed_balance_can_serve_as_the_opening() -> None:
    header = _camt_header(
        CAMT053Balance("PRCD", "CRDT", "2026-03-15", "INR", "10.00"),
        CAMT053Balance("CLBD", "CRDT", "2026-03-16", "INR", "20.00"),
    )
    assert camt_balances(header) is not None


def test_camt_without_both_balances_gives_none() -> None:
    assert camt_balances(_camt_header(CAMT053Balance("OPBD", "CRDT", None, "INR", "1.00"))) is None
    assert camt_balances(_camt_header()) is None


def test_a_zero_decimal_currency_uses_its_own_exponent() -> None:
    header = MT940StatementHeader(
        None, None, None,
        MT940Balance("60F", "C", "260315", "JPY", "1000"),
        MT940Balance("62F", "C", "260316", "JPY", "1500"),
    )  # fmt: skip
    balances = mt940_balances(header, ",")
    assert balances is not None and (balances.opening_minor, balances.closing_minor) == (1000, 1500)
    assert check_statement(balances, [_row("C", "500")], ",").balanced is True
