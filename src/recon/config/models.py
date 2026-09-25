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

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator


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

    @field_validator("timezone")
    @classmethod
    def _valid_iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError(f"{value!r} is not a known IANA timezone") from None
        return value

    @field_validator("currency_default")
    @classmethod
    def _uppercase_currency(cls, value: str) -> str:
        return value.upper()
