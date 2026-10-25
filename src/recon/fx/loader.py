# src/recon/fx/loader.py
"""Parses an exchange-rate CSV (D10's fx_rates file) and flags suspect rates.

Columns: date, currency, rate (INR per one unit), and optionally source
(default RBI). The date may be ISO (2026-03-15) or DD-MM-YYYY. Rows that cannot
be parsed are reported with their line number and skipped; they never abort the
file.

A rate more than `outlier_percent` away from the median of that currency and
source in the same file is marked suspect. D10 plants three wrong rates in
450, and a median is not moved by a few of them the way a mean would be. With
fewer than MIN_POINTS rates for a currency there is no meaningful median, so
nothing is flagged.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import IO

from recon.persistence.models.fx import FX_SOURCES

MIN_POINTS = 3
DEFAULT_OUTLIER_PERCENT = Decimal(5)
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y")
_MAX_RATE = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class ParsedFxRate:
    line_no: int
    rate_date: date
    currency: str
    rate_inr: Decimal
    source: str
    suspect: bool = False
    suspect_reason: str | None = None


@dataclass(frozen=True, slots=True)
class FxLoadError:
    line_no: int
    detail: str


@dataclass(slots=True)
class FxLoadResult:
    rates: list[ParsedFxRate] = field(default_factory=list)
    errors: list[FxLoadError] = field(default_factory=list)

    @property
    def suspects(self) -> list[ParsedFxRate]:
        return [r for r in self.rates if r.suspect]


def _parse_date(text: str) -> date:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    raise ValueError(f"unrecognised date {text!r}")


def _parse_row(line_no: int, row: dict[str, str]) -> ParsedFxRate:
    currency = (row.get("currency") or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():  # noqa: PLR2004
        raise ValueError(f"invalid currency {row.get('currency')!r}")
    raw_rate = (row.get("rate") or row.get("rate_inr") or "").strip()
    try:
        rate = Decimal(raw_rate)
    except InvalidOperation:
        raise ValueError(f"invalid rate {raw_rate!r}") from None
    if not rate.is_finite() or rate <= 0 or rate > _MAX_RATE:
        raise ValueError(f"rate {raw_rate!r} is out of range")
    source = (row.get("source") or "RBI").strip().upper()
    if source not in FX_SOURCES:
        raise ValueError(f"unknown source {source!r}")
    return ParsedFxRate(line_no, _parse_date(row.get("date") or ""), currency, rate, source)


def parse_fx_csv(
    stream: IO[str], *, outlier_percent: Decimal = DEFAULT_OUTLIER_PERCENT
) -> FxLoadResult:
    reader = csv.DictReader(stream)
    fields = {f.strip().lower() for f in reader.fieldnames or []}
    if not {"date", "currency"} <= fields or not ({"rate", "rate_inr"} & fields):
        raise ValueError("fx file needs the columns: date, currency, rate")
    reader.fieldnames = [f.strip().lower() for f in reader.fieldnames or []]

    result = FxLoadResult()
    for line_no, row in enumerate(reader, start=2):
        try:
            result.rates.append(_parse_row(line_no, row))
        except ValueError as exc:
            result.errors.append(FxLoadError(line_no, str(exc)))
    _flag_outliers(result, outlier_percent)
    return result


def _flag_outliers(result: FxLoadResult, outlier_percent: Decimal) -> None:
    groups: dict[tuple[str, str], list[Decimal]] = defaultdict(list)
    for r in result.rates:
        groups[(r.currency, r.source)].append(r.rate_inr)
    medians = {k: median(v) for k, v in groups.items() if len(v) >= MIN_POINTS}
    for i, r in enumerate(result.rates):
        mid = medians.get((r.currency, r.source))
        if mid is None:
            continue
        deviation = abs(r.rate_inr - mid) / mid * 100
        if deviation > outlier_percent:
            result.rates[i] = ParsedFxRate(
                r.line_no,
                r.rate_date,
                r.currency,
                r.rate_inr,
                r.source,
                suspect=True,
                suspect_reason=f"{deviation:.1f}% from the file median {mid}",
            )
