# src/recon/normalisation/pipeline.py
"""NormalisationPipeline: composes the five normalisation functions
(timezone, currency, reference, amount, counterparty — R37) plus
direction normalisation, against a single ParsedRow, producing a
CanonicalTransaction (WP1's domain model).

Per-format quirks are resolved here, not inside the individual
normalisation functions, which stay format-agnostic:
  - date format: CSV uses the bank's configured csv.date_format; MT940 is
    always YYMMDD; CAMT.053 is always ISO YYYY-MM-DD (BookgDt/Dt, ValDt/Dt).
  - amount separators: CSV/MT940 come from bank config; CAMT.053 amounts
    are always period-decimal per the ISO 20022 standard itself.
  - direction vocabulary: see recon.normalisation.direction.

txn_date and settlement_date use `NormalisedTimestamp.local_date` (the
date as it appears on the source statement), never `.utc.date()` — see
recon.normalisation.timestamps' module docstring for why the latter
silently shifts every date backward by one day for any bank in a
positive UTC-offset zone. This distinction is the reason WP3 Increment 1
was revisited in this increment.

A ParsedRow carrying existing parse errors is never normalised —
normalise() raises NormalisationError immediately rather than guess at a
value for a field that already failed structural parsing. Every
individual normalisation-function error (bad direction text, unknown
currency, unparseable amount, unparseable date) is likewise caught and
re-raised as NormalisationError, so a caller of this pipeline has exactly
one exception type to handle, not five.

format_type is typed as the domain SourceFormat enum here, not the plain
strings ("CSV"/"MT940"/"CAMT053") that recon.ingestion.service.IngestionService
currently passes around (StrEnum members compare equal to their string
value, so this is compatible, but a future increment that wires this
pipeline directly into the ingestion/persistence flow will need an
explicit SourceFormat(raw_format_type) conversion at that boundary).
"""

from __future__ import annotations

import uuid

from recon.config.models import BankConfig
from recon.domain.canonical import CanonicalTransaction
from recon.domain.enums import SourceFormat, TransactionSource
from recon.domain.money import currency_exponent
from recon.ingestion.parsed_row import ParsedRow
from recon.normalisation.amounts import AmountNormalisationError, normalise_amount
from recon.normalisation.counterparty import clean_counterparty_name
from recon.normalisation.currency import CurrencyNormalisationError, resolve_currency
from recon.normalisation.direction import DirectionNormalisationError, normalise_direction
from recon.normalisation.reference import clean_reference
from recon.normalisation.timestamps import TimestampNormalisationError, normalise_timestamp

_MT940_DATE_FORMAT = "%y%m%d"
_ISO_DATE_FORMAT = "%Y-%m-%d"

_NormalisationSubErrors = (
    TimestampNormalisationError,
    DirectionNormalisationError,
    CurrencyNormalisationError,
    AmountNormalisationError,
)


class NormalisationError(ValueError):
    """The single exception type a caller of NormalisationPipeline needs
    to catch — either the row already carried parse errors, its bank
    config is missing the section its format requires, or one of the
    individual normalisation steps failed on otherwise structurally-valid
    text."""


class NormalisationPipeline:
    def normalise(
        self,
        row: ParsedRow,
        config: BankConfig,
        format_type: SourceFormat,
        source: TransactionSource,
        ingestion_file_id: str,
    ) -> CanonicalTransaction:
        if not row.is_valid:
            raise NormalisationError(
                f"line {row.line_no}: cannot normalise a row with existing "
                f"parse errors: {[e.detail for e in row.errors]}"
            )

        try:
            return self._normalise_valid_row(row, config, format_type, source, ingestion_file_id)
        except _NormalisationSubErrors as exc:
            raise NormalisationError(f"line {row.line_no}: {exc}") from exc

    def _normalise_valid_row(
        self,
        row: ParsedRow,
        config: BankConfig,
        format_type: SourceFormat,
        source: TransactionSource,
        ingestion_file_id: str,
    ) -> CanonicalTransaction:
        date_format = self._date_format_for(format_type, config)
        timestamp = normalise_timestamp(row.mapped["txn_date"], date_format, config.timezone)

        direction, is_reversal = normalise_direction(row.mapped["direction"])

        currency_code, currency_source = resolve_currency(
            row.mapped.get("currency"), None, config.currency_default
        )

        decimal_separator, thousands_separator = self._amount_separators_for(format_type, config)
        money = normalise_amount(
            row.mapped["amount"],
            currency_code,
            decimal_separator=decimal_separator,
            thousands_separator=thousands_separator,
        )

        normalised_reference = clean_reference(row.mapped["reference"])

        counterparty_name = row.mapped.get("counterparty_name")
        counterparty_name_normalised = (
            clean_counterparty_name(counterparty_name) if counterparty_name else None
        )

        settlement_date = None
        settlement_date_text = row.mapped.get("settlement_date")
        if settlement_date_text:
            settlement_timestamp = normalise_timestamp(
                settlement_date_text, date_format, config.timezone
            )
            settlement_date = settlement_timestamp.local_date

        return CanonicalTransaction(
            id=str(uuid.uuid4()),
            source=source,
            bank_code=config.bank_code,
            format_type=format_type,
            ingestion_file_id=ingestion_file_id,
            source_line_no=row.line_no,
            config_version=config.config_version,
            txn_id=row.mapped["reference"],
            txn_date=timestamp.local_date,
            amount=money.amount,
            amount_minor=money.minor_units,
            currency=currency_code,
            currency_exponent_value=currency_exponent(currency_code),
            currency_source=currency_source,
            original_currency=row.mapped.get("currency"),
            direction=direction,
            is_reversal=is_reversal,
            counterparty_name=counterparty_name,
            counterparty_name_normalised=counterparty_name_normalised,
            narration=row.mapped.get("narration"),
            settlement_date=settlement_date,
            original_amount_text=row.mapped["amount"],
            txn_timestamp_utc=timestamp.utc,
            txn_timestamp_original=timestamp.original_text,
            source_timezone=config.timezone,
            original_reference_text=row.mapped["reference"],
            normalised_reference=normalised_reference,
        )

    @staticmethod
    def _date_format_for(format_type: SourceFormat, config: BankConfig) -> str:
        if format_type == SourceFormat.CSV:
            if config.csv is None:
                raise NormalisationError(f"{config.bank_code}: no CSV configuration present")
            return config.csv.date_format
        if format_type == SourceFormat.MT940:
            return _MT940_DATE_FORMAT
        if format_type == SourceFormat.CAMT053:
            return _ISO_DATE_FORMAT
        raise NormalisationError(f"unsupported format_type {format_type!r}")

    @staticmethod
    def _amount_separators_for(
        format_type: SourceFormat, config: BankConfig
    ) -> tuple[str, str | None]:
        if format_type == SourceFormat.CSV:
            if config.csv is None:
                raise NormalisationError(f"{config.bank_code}: no CSV configuration present")
            return config.csv.amount_decimal_separator, config.csv.amount_thousands_separator
        if format_type == SourceFormat.MT940:
            if config.mt940 is None:
                raise NormalisationError(f"{config.bank_code}: no MT940 configuration present")
            return config.mt940.decimal_separator, None
        if format_type == SourceFormat.CAMT053:
            return ".", None  # ISO 20022 amounts are always period-decimal
        raise NormalisationError(f"unsupported format_type {format_type!r}")
