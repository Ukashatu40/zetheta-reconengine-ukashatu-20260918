# src/recon/ingestion/parsers/mt940/state_machine.py
"""MT940 state-machine parser (R23).

Assembles the lexer's flat (tag, content) sequence into per-transaction
ParsedRow objects, pairing each :61: statement line with the :86:
narrative that immediately follows it (if any). Header fields (:20:,
:25:, :28C:, :60F:/:60M:, :62F:/:62M:) are captured as
MT940StatementHeader / MT940Balance and returned alongside the rows.
Wiring these into a statement_balances table (for the balance-level
reconciliation control from Case Study C5) is deferred to a later
increment — the same phased split CSVParser (parser) and
IngestionService (persistence) followed.

Five bank-specific deviations (PDF Day 2) and how each is actually
handled:

1. Missing funds code       — handled structurally by field_61's regex.
                               Funds code is alpha, amount is digits;
                               the boundary is unambiguous with no
                               configuration needed. SBI's sample data
                               needs nothing special.
2. Period decimal separator — configurable via
                               BankConfig.mt940.decimal_separator
                               (Axis: "."; default: ",") — this one IS
                               genuinely ambiguous without configuration,
                               unlike (1).
3. Non-standard :86: sub-field structure — :86: content is captured as
                               raw narrative text only; no sub-field
                               extraction (counterparty name/account)
                               happens at the parsing stage. See AE-01:
                               guessing a fixed line offset into :86: is
                               exactly the error the PDF plants.
                               Structured extraction is deferred to
                               normalisation (WP3), via a per-bank
                               configurable strategy — never this parser.
4. Extended :61: reference   — reference_raw captures the entire
                               remainder of the line after the
                               transaction-type code, with no length
                               truncation applied at parse time.
5. Multi-currency statements — not extracted per :61: line in this
                               increment (base MT940 doesn't carry
                               currency there). The statement's
                               :60F:/:60M: currency is exposed via
                               MT940Balance; a bank whose :61: lines
                               embed a currency code per entry is out of
                               scope here and documented as a known
                               limitation — none of the D10 sample banks
                               require it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import IO

from recon.config.models import BankConfig
from recon.ingestion.parsed_row import ParsedRow, ParseError, ParseErrorCode
from recon.ingestion.parsers.mt940.field_61 import Field61ParseError, parse_field_61
from recon.ingestion.parsers.mt940.lexer import LexedField, MT940LexError, lex


class MT940ParseError(ValueError):
    """File-level problems that make parsing impossible at all — mirrors
    CSVParseError's role for the CSV parser."""


@dataclass(frozen=True, slots=True)
class MT940Balance:
    tag: str  # 60F, 60M, 62F, 62M
    mark: str  # D or C
    date_raw: str  # YYMMDD
    currency: str
    amount_raw: str


@dataclass(frozen=True, slots=True)
class MT940StatementHeader:
    transaction_reference: str | None  # :20:
    account_id: str | None  # :25:
    statement_number: str | None  # :28C:
    opening_balance: MT940Balance | None
    closing_balance: MT940Balance | None


def _parse_balance(tag: str, content: str) -> MT940Balance:
    # Structural: 1!a (mark) 6!n (date) 3!a (currency) 15d (amount) is the
    # SWIFT standard's own fixed-width layout for this field — this
    # slicing reflects the spec, not a parser-invented offset (unlike the
    # :86: line-offset guessing this codebase explicitly avoids, AE-01).
    mark = content[0]
    date_raw = content[1:7]
    currency = content[7:10]
    amount_raw = content[10:]
    return MT940Balance(
        tag=tag, mark=mark, date_raw=date_raw, currency=currency, amount_raw=amount_raw
    )


class MT940Parser:
    def parse(self, stream: IO[str], config: BankConfig) -> Iterator[ParsedRow]:
        """Protocol-conforming entry point (see recon.ingestion.protocol.Parser)
        — rows only. Use parse_with_header() when the statement header and
        balances are also needed."""
        _, rows = self.parse_with_header(stream, config)
        yield from rows

    def parse_with_header(
        self, stream: IO[str], config: BankConfig
    ) -> tuple[MT940StatementHeader, list[ParsedRow]]:
        if config.mt940 is None:
            raise MT940ParseError(f"{config.bank_code}: no MT940 configuration present")
        mt940_config = config.mt940

        text = stream.read()
        try:
            fields = lex(text)
        except MT940LexError as exc:
            raise MT940ParseError(str(exc)) from exc

        header = self._extract_header(fields)
        rows = list(self._extract_rows(fields, mt940_config.decimal_separator))
        return header, rows

    def _extract_header(self, fields: list[LexedField]) -> MT940StatementHeader:
        transaction_reference: str | None = None
        account_id: str | None = None
        statement_number: str | None = None
        opening: MT940Balance | None = None
        closing: MT940Balance | None = None

        for field in fields:
            if field.tag == "20":
                transaction_reference = field.content.strip()
            elif field.tag == "25":
                account_id = field.content.strip()
            elif field.tag == "28C":
                statement_number = field.content.strip()
            elif field.tag in ("60F", "60M"):
                opening = _parse_balance(field.tag, field.content)
            elif field.tag in ("62F", "62M"):
                closing = _parse_balance(field.tag, field.content)

        return MT940StatementHeader(
            transaction_reference=transaction_reference,
            account_id=account_id,
            statement_number=statement_number,
            opening_balance=opening,
            closing_balance=closing,
        )

    def _extract_rows(
        self, fields: list[LexedField], decimal_separator: str
    ) -> Iterator[ParsedRow]:
        i = 0
        while i < len(fields):
            field = fields[i]
            if field.tag != "61":
                i += 1
                continue

            narration = ""
            if i + 1 < len(fields) and fields[i + 1].tag == "86":
                narration = fields[i + 1].content.strip()
                i += 1  # consume the paired :86: too

            yield self._build_row(field, narration, decimal_separator)
            i += 1

    def _build_row(self, field_61: LexedField, narration: str, decimal_separator: str) -> ParsedRow:
        errors: list[ParseError] = []
        raw_fields = {"61": field_61.content, "86": narration}
        mapped: dict[str, str] = {}

        try:
            parsed = parse_field_61(field_61.content)
        except Field61ParseError as exc:
            errors.append(
                ParseError(code=ParseErrorCode.MALFORMED_FIELD, field=None, detail=str(exc))
            )
            return ParsedRow(
                line_no=field_61.line_no, raw_fields=raw_fields, mapped=mapped, errors=errors
            )

        mapped["reference"] = parsed.reference_raw
        mapped["txn_date"] = parsed.value_date_raw
        mapped["amount"] = parsed.amount_raw
        mapped["direction"] = parsed.mark
        mapped["narration"] = narration

        if not parsed.reference_raw.strip():
            errors.append(
                ParseError(
                    code=ParseErrorCode.MISSING_REQUIRED_FIELD,
                    field="reference",
                    detail="customer reference is empty",
                )
            )

        try:
            datetime.strptime(parsed.value_date_raw, "%y%m%d")  # noqa: DTZ007
        except ValueError:
            errors.append(
                ParseError(
                    code=ParseErrorCode.INVALID_DATE,
                    field="txn_date",
                    detail=f"{parsed.value_date_raw!r} is not a valid YYMMDD date",
                )
            )

        normalised_amount = parsed.amount_raw
        if decimal_separator != ".":
            normalised_amount = normalised_amount.replace(decimal_separator, ".")
        try:
            Decimal(normalised_amount)
        except InvalidOperation:
            errors.append(
                ParseError(
                    code=ParseErrorCode.INVALID_AMOUNT,
                    field="amount",
                    detail=f"{parsed.amount_raw!r} could not be parsed as a decimal amount",
                )
            )

        return ParsedRow(
            line_no=field_61.line_no, raw_fields=raw_fields, mapped=mapped, errors=errors
        )
