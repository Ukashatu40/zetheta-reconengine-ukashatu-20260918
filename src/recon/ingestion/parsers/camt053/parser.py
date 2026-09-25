# src/recon/ingestion/parsers/camt053/parser.py
"""CAMT.053 XML parser (R25-R28).

Root traversal: BkToCstmrStmt/Stmt/Ntry, where each Ntry may contain
multiple NtryDtls, each of which may contain multiple TxDtls (R28's
"recursive extraction for nested NtryDtls containing multiple TxDtls").
An Ntry with no NtryDtls at all is still a valid, parseable row — the
entry-level amount/date/direction are used directly and TxDtls-only
fields (end-to-end ID, structured remittance) are left absent rather than
treated as an error.

AE-02: settlement_date is sourced from Ntry/ValDt/Dt — the PER-ENTRY value
date — never from the statement-level Stmt/Bal[.../Cd='CLBD'] element the
PDF's A2.4 table points to.

Streams are read as bytes (IO[bytes]), not text (IO[str]): lxml uses the
XML prolog's own declared encoding to decode the document correctly, so
pre-decoding upstream (as the CSV parser's open_stream does) would bypass
that and risk mis-decoding a file whose declared encoding isn't UTF-8.
This means CAMT053Parser does not structurally satisfy
recon.ingestion.protocol.Parser (which is typed for IO[str], matching
CSVParser/MT940Parser) — nothing currently relies on that structural
match; a future dispatch layer (IngestionService, WP2 Increment 5) opens
CAMT.053 files in binary mode explicitly rather than going through the
shared text-mode Parser protocol.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import IO

from lxml import etree

from recon.config.models import BankConfig
from recon.ingestion.parsed_row import ParsedRow, ParseError, ParseErrorCode
from recon.ingestion.parsers.camt053.xml_safety import get_safe_parser
from recon.ingestion.parsers.camt053.xpath import CAMT053_NAMESPACE, find, find_all, text_of


class CAMT053ParseError(ValueError):
    """File-level problems that make parsing impossible at all — malformed
    XML, wrong root element, or a document that isn't CAMT.053 shaped at
    all. Mirrors CSVParseError/MT940ParseError's role."""


@dataclass(frozen=True, slots=True)
class CAMT053Balance:
    type_code: str
    credit_debit_indicator: str
    date_raw: str | None
    currency: str | None
    amount_raw: str | None


@dataclass(frozen=True, slots=True)
class CAMT053StatementHeader:
    message_id: str | None
    account_id: str | None
    statement_id: str | None
    balances: list[CAMT053Balance]


class CAMT053Parser:
    def parse(self, stream: IO[bytes], config: BankConfig) -> Iterator[ParsedRow]:
        """Rows only. Use parse_with_header() when the statement header and
        balances are also needed."""
        _, rows = self.parse_with_header(stream, config)
        yield from rows

    def parse_with_header(
        self, stream: IO[bytes], _config: BankConfig
    ) -> tuple[CAMT053StatementHeader, list[ParsedRow]]:
        parser = get_safe_parser()
        try:
            tree = etree.parse(stream, parser=parser)
        except etree.XMLSyntaxError as exc:
            raise CAMT053ParseError(f"malformed XML: {exc}") from exc

        root = tree.getroot()
        root_qname = etree.QName(root)
        if root_qname.namespace != CAMT053_NAMESPACE or root_qname.localname != "BkToCstmrStmt":
            raise CAMT053ParseError(
                "root element is not a namespace-qualified BkToCstmrStmt "
                f"(got {root.tag!r}); this does not look like a CAMT.053 statement"
            )

        stmt = find(root, "c:Stmt")
        if stmt is None:
            raise CAMT053ParseError("BkToCstmrStmt/Stmt element is missing")

        header = self._extract_header(root, stmt)
        rows = list(self._extract_rows(stmt))
        return header, rows

    def _extract_header(
        self, bk_to_cstmr_stmt: etree._Element, stmt: etree._Element
    ) -> CAMT053StatementHeader:
        grp_hdr = find(bk_to_cstmr_stmt, "c:GrpHdr")
        message_id = text_of(find(grp_hdr, "c:MsgId")) if grp_hdr is not None else None

        statement_id = text_of(find(stmt, "c:Id"))

        account_id = None
        acct = find(stmt, "c:Acct")
        if acct is not None:
            iban = find(acct, "c:Id/c:IBAN")
            account_id = text_of(iban)
            if account_id is None:
                account_id = text_of(find(acct, "c:Id/c:Othr/c:Id"))

        balances = [self._parse_balance(bal) for bal in find_all(stmt, "c:Bal")]

        return CAMT053StatementHeader(
            message_id=message_id,
            account_id=account_id,
            statement_id=statement_id,
            balances=balances,
        )

    def _parse_balance(self, bal: etree._Element) -> CAMT053Balance:
        type_code = text_of(find(bal, "c:Tp/c:CdOrPrtry/c:Cd")) or ""
        cd_ind = text_of(find(bal, "c:CdtDbtInd")) or ""
        date_raw = text_of(find(bal, "c:Dt/c:Dt"))
        amt_el = find(bal, "c:Amt")
        currency = amt_el.get("Ccy") if amt_el is not None else None
        amount_raw = text_of(amt_el)
        return CAMT053Balance(
            type_code=type_code,
            credit_debit_indicator=cd_ind,
            date_raw=date_raw,
            currency=currency,
            amount_raw=amount_raw,
        )

    def _extract_rows(self, stmt: etree._Element) -> Iterator[ParsedRow]:
        for index, ntry in enumerate(find_all(stmt, "c:Ntry"), start=1):
            yield self._build_row(index, ntry)

    def _build_row(self, index: int, ntry: etree._Element) -> ParsedRow:
        raw_fields, mapped = self._extract_entry_level_fields(ntry)
        end_to_end_id, counterparty_name, narration_parts = self._extract_tx_details(ntry)
        self._finalise_mapped_fields(
            ntry, mapped, end_to_end_id, counterparty_name, narration_parts
        )
        raw_fields["entry_index"] = str(index)
        errors = self._validate_row(mapped)
        return ParsedRow(line_no=index, raw_fields=raw_fields, mapped=mapped, errors=errors)

    def _extract_entry_level_fields(
        self, ntry: etree._Element
    ) -> tuple[dict[str, str], dict[str, str]]:
        amount_el = find(ntry, "c:Amt")
        amount_raw = text_of(amount_el)
        currency = amount_el.get("Ccy") if amount_el is not None else None
        cd_ind = text_of(find(ntry, "c:CdtDbtInd"))
        bookg_dt = text_of(find(ntry, "c:BookgDt/c:Dt"))
        val_dt = text_of(find(ntry, "c:ValDt/c:Dt"))  # AE-02: per-entry, not statement-level

        raw_fields = {
            "amount": amount_raw or "",
            "currency": currency or "",
            "direction": cd_ind or "",
            "bookg_dt": bookg_dt or "",
            "val_dt": val_dt or "",
        }
        mapped = {
            "txn_date": bookg_dt or "",
            "settlement_date": val_dt or "",  # AE-02: distinct from txn_date
            "amount": amount_raw or "",
            "currency": currency or "",
            "direction": cd_ind or "",
        }
        return raw_fields, mapped

    def _extract_tx_details(self, ntry: etree._Element) -> tuple[str | None, str | None, list[str]]:
        """R28: recursive extraction across every TxDtls under every
        NtryDtls — an Ntry may legitimately contain several."""
        end_to_end_id: str | None = None
        counterparty_name: str | None = None
        narration_parts: list[str] = []

        for ntry_dtls in find_all(ntry, "c:NtryDtls"):
            for tx_dtls in find_all(ntry_dtls, "c:TxDtls"):
                if end_to_end_id is None:
                    end_to_end_id = text_of(find(tx_dtls, "c:Refs/c:EndToEndId"))
                if counterparty_name is None:
                    counterparty_name = text_of(find(tx_dtls, "c:RltdPties/c:Cdtr/c:Nm"))
                addtl_info = text_of(find(tx_dtls, "c:AddtlTxInf"))
                if addtl_info:
                    narration_parts.append(addtl_info)

        return end_to_end_id, counterparty_name, narration_parts

    def _finalise_mapped_fields(
        self,
        ntry: etree._Element,
        mapped: dict[str, str],
        end_to_end_id: str | None,
        counterparty_name: str | None,
        narration_parts: list[str],
    ) -> None:
        addtl_ntry_inf = text_of(find(ntry, "c:AddtlNtryInf"))
        if addtl_ntry_inf:
            narration_parts.append(addtl_ntry_inf)

        mapped["reference"] = end_to_end_id or ""
        if counterparty_name:
            mapped["counterparty_name"] = counterparty_name
        if narration_parts:
            mapped["narration"] = " | ".join(narration_parts)

    def _validate_row(self, mapped: dict[str, str]) -> list[ParseError]:
        errors: list[ParseError] = []

        if not mapped.get("reference", "").strip():
            errors.append(
                ParseError(
                    code=ParseErrorCode.MISSING_REQUIRED_FIELD,
                    field="reference",
                    detail="no EndToEndId found under any NtryDtls/TxDtls",
                )
            )
        if not mapped.get("txn_date"):
            errors.append(
                ParseError(
                    code=ParseErrorCode.MISSING_REQUIRED_FIELD,
                    field="txn_date",
                    detail="Ntry/BookgDt/Dt is missing",
                )
            )
        if mapped.get("direction") not in ("CRDT", "DBIT"):
            errors.append(
                ParseError(
                    code=ParseErrorCode.MALFORMED_FIELD,
                    field="direction",
                    detail=f"CdtDbtInd={mapped.get('direction')!r} is neither CRDT nor DBIT",
                )
            )
        if not mapped.get("currency"):
            errors.append(
                ParseError(
                    code=ParseErrorCode.MISSING_REQUIRED_FIELD,
                    field="currency",
                    detail="Amt/@Ccy attribute is missing",
                )
            )
        errors.extend(self._validate_amount(mapped.get("amount", "")))
        return errors

    def _validate_amount(self, amount_raw: str) -> list[ParseError]:
        if not amount_raw:
            return [
                ParseError(
                    code=ParseErrorCode.MISSING_REQUIRED_FIELD,
                    field="amount",
                    detail="Ntry/Amt is missing",
                )
            ]
        try:
            Decimal(amount_raw)
        except InvalidOperation:
            return [
                ParseError(
                    code=ParseErrorCode.INVALID_AMOUNT,
                    field="amount",
                    detail=f"{amount_raw!r} could not be parsed as a decimal amount",
                )
            ]
        return []
