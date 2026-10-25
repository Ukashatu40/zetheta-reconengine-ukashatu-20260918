# src/recon/config/models.py
"""Pydantic models for bank configuration.

Config authoring is validated twice, deliberately: first against
config/banks/_schema.json (R45 — generic structural validation, runnable
without importing recon at all), then parsed into these typed models for
everything the codebase actually consumes. The two layers catch different
mistakes: the JSON Schema catches "this file doesn't even have the right
shape"; pydantic catches "this value is the right shape but semantically
wrong" (e.g. an unknown IANA timezone name).
"""

from __future__ import annotations

import re
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SETTLEMENT_CYCLE = re.compile(r"^T\+(\d+)$")


class Mt940FormatConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    decimal_separator: str


class CsvFormatConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    delimiter: str = ","
    encoding: str = "utf-8"
    has_header: bool = True
    column_mapping: dict[str, str]
    date_format: str
    amount_decimal_separator: str = "."
    amount_thousands_separator: str | None = None

    @field_validator("column_mapping")
    @classmethod
    def _requires_core_fields(cls, value: dict[str, str]) -> dict[str, str]:
        required = {"reference", "txn_date", "amount", "direction"}
        missing = required - value.keys()
        if missing:
            raise ValueError(f"column_mapping missing required canonical fields: {sorted(missing)}")
        return value


class FeeScheduleConfig(BaseModel):
    """The bank's published fee: a fixed amount plus a percentage of the
    transaction amount, matched within tolerance_minor (FEE_DEDUCTION, A4.1 #11)."""

    model_config = ConfigDict(frozen=True)

    fixed_minor: int = Field(default=0, ge=0)
    percent: Decimal = Field(default=Decimal(0), ge=0)
    tolerance_minor: int = Field(default=1, ge=0)


class BankConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    bank_code: str
    bank_name: str
    config_version: str
    supported_formats: list[str]
    timezone: str
    currency_default: str
    settlement_cycle: str
    reconciliation_window_days: int
    csv: CsvFormatConfig | None = None
    mt940: Mt940FormatConfig | None = None
    # Matching behaviour, per bank (A3.3). All optional: absent means the default.
    calendar: str = "in"
    date_offset_business_days: int | None = Field(default=None, ge=0)
    amount_tolerance_minor: int = Field(default=1, ge=0)
    fx_tolerance_percent: Decimal = Field(default=Decimal("1.5"), gt=0)
    fee_schedule: FeeScheduleConfig | None = None
    expected_settlement_time: str | None = None  # "HH:MM" in the bank's timezone (Absence Alert)

    @property
    def settlement_days(self) -> int:
        """N of a "T+N" settlement_cycle; a cycle in any other form counts as T+1."""
        match = _SETTLEMENT_CYCLE.match(self.settlement_cycle.strip().upper())
        return int(match.group(1)) if match else 1

    @property
    def date_offset_days(self) -> int:
        """Business days a date may differ by under the Date Offset Rule."""
        if self.date_offset_business_days is not None:
            return self.date_offset_business_days
        return max(1, self.settlement_days)

    @field_validator("timezone")
    @classmethod
    def _valid_iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError(f"{value!r} is not a known IANA timezone") from None
        return value

    @field_validator("expected_settlement_time")
    @classmethod
    def _valid_clock_time(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError(f"{value!r} is not an HH:MM time")
        return value

    @field_validator("currency_default")
    @classmethod
    def _uppercase_currency(cls, value: str) -> str:
        return value.upper()
