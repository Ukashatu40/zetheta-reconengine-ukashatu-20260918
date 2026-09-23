# src/recon/domain/canonical.py
"""The canonical transaction model.

Implements the 10-field model from PDF A2.4 (R36), extended with the
preservation fields A1.2/A5.2 require ("must maintain both original/raw
values and normalised values" / "preserve original timestamp") and with
fields that correct specific errors documented in
docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from recon.domain.enums import CurrencySource, Direction, SourceFormat, TransactionSource
from recon.domain.money import UnknownCurrencyError, currency_exponent


class CanonicalTransaction(BaseModel):
    """A transaction normalised into the canonical model.

    Field-by-field mapping to PDF A2.4's ten canonical fields, plus the
    corrections noted inline. This model is intentionally infrastructure-
    free: no SQLAlchemy, no FastAPI. recon.persistence.models translates to
    and from this at the repository boundary.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    # -- identity ------------------------------------------------------
    id: str = Field(
        description=(
            "System-generated identity (UUID string). AE-15: txn_id below is "
            "a source reference and is NOT assumed unique across banks or "
            "statements, so it cannot serve as a primary key."
        )
    )
    source: TransactionSource
    bank_code: str
    format_type: SourceFormat
    ingestion_file_id: str
    source_line_no: int | None = None
    config_version: str

    # -- A2.4 field 1: txn_id -------------------------------------------
    txn_id: str = Field(
        description=(
            "Source-provided reference (MT940 :61: ref / CAMT EndToEndId / "
            "CSV reference column). Indexed but not unique — see id above."
        )
    )
    bank_ref: str | None = Field(
        default=None,
        description="Bank's own internal reference (MT940 :20: / CAMT GrpHdr/MsgId).",
    )

    # -- A2.4 field 2: txn_date ------------------------------------------
    txn_date: date

    # -- A2.4 field 3/4: amount / currency, corrected per AE-17 ----------
    amount: Decimal = Field(description="Amount in major units, positive, NUMERIC(20,4).")
    amount_minor: int = Field(description="Same amount in integer minor units. See Money.")
    currency: str = Field(min_length=3, max_length=3)
    currency_exponent_value: int = Field(
        description="Minor-unit exponent applied for this currency (0/2/3)."
    )
    currency_source: CurrencySource = Field(
        description="AE-13: where currency was actually resolved from for this record."
    )
    original_currency: str | None = Field(
        default=None,
        description="Currency as it appeared in the source, before any conversion.",
    )

    # -- A2.4 field 5: direction, corrected per AE-12 --------------------
    direction: Direction
    is_reversal: bool = Field(
        default=False,
        description="AE-12: true for MT940 RD/RC marks. Direction itself stays DR/CR.",
    )
    reverses_reference: str | None = Field(
        default=None, description="txn_id of the entry this reversal reverses, if known."
    )

    # -- A2.4 field 6/7: counterparty name / account ---------------------
    counterparty_name: str | None = None
    counterparty_name_normalised: str | None = None
    counterparty_account: str | None = Field(
        default=None,
        description=(
            "AE-01: only populated when extraction confidence is high. "
            "Never guessed from a fixed :86: line offset."
        ),
    )
    counterparty_account_masked: str | None = Field(
        default=None, description="Last-4-only view, per AE-26 PAN masking."
    )

    # -- A2.4 field 8: bank_ref — see above, merged with txn_id block ----

    # -- A2.4 field 9: narration ------------------------------------------
    narration: str | None = None
    narration_normalised: str | None = None

    # -- A2.4 field 10: settlement_date, corrected per AE-02 -------------
    settlement_date: date | None = Field(
        default=None,
        description=(
            "AE-02: per-transaction value date (CAMT Ntry/ValDt/Dt or MT940 "
            ":61: value date), NOT the statement-level closing-balance date. "
            "Statement-level balances live in recon.persistence StatementBalance."
        ),
    )

    # -- preservation fields (A1.2, A5.2) ---------------------------------
    original_amount_text: str | None = Field(
        default=None, description="Amount exactly as it appeared in the source, unparsed."
    )
    txn_timestamp_utc: datetime = Field(description="Normalised to UTC.")
    txn_timestamp_original: str = Field(
        description="Original timestamp string, unmodified, before any timezone conversion."
    )
    source_timezone: str = Field(
        description="IANA timezone name used for the UTC conversion, e.g. 'Asia/Kolkata'."
    )
    original_reference_text: str | None = Field(
        default=None, description="Reference exactly as it appeared before cleaning."
    )
    normalised_reference: str | None = None

    # -- matching workflow state -------------------------------------------
    match_status: str = Field(default="UNMATCHED")

    # ------------------------------------------------------------------
    # validators
    # ------------------------------------------------------------------

    @field_validator("currency", "original_currency")
    @classmethod
    def _uppercase_currency(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @field_validator("amount")
    @classmethod
    def _amount_non_negative(cls, value: Decimal) -> Decimal:
        # AE-05 / D2 normalisation rule: sign is carried by `direction`,
        # never by the amount itself. A negative amount here means an
        # upstream parser failed to separate sign from magnitude.
        if value < 0:
            raise ValueError(
                f"amount must be non-negative; sign belongs on `direction`, got {value}"
            )
        return value

    @model_validator(mode="after")
    def _validate_currency_exponent_consistency(self) -> CanonicalTransaction:
        try:
            expected_exponent = currency_exponent(self.currency)
        except UnknownCurrencyError as exc:
            raise ValueError(str(exc)) from None

        if expected_exponent != self.currency_exponent_value:
            raise ValueError(
                f"currency_exponent_value={self.currency_exponent_value} does not "
                f"match the registered exponent {expected_exponent} for {self.currency}"
            )

        expected_minor = int(self.amount.scaleb(expected_exponent))
        if expected_minor != self.amount_minor:
            raise ValueError(
                f"amount_minor={self.amount_minor} inconsistent with amount="
                f"{self.amount} at exponent {expected_exponent} "
                f"(expected {expected_minor})"
            )
        return self

    @model_validator(mode="after")
    def _validate_reversal_fields(self) -> CanonicalTransaction:
        if not self.is_reversal and self.reverses_reference is not None:
            raise ValueError("reverses_reference set but is_reversal is False")
        return self
