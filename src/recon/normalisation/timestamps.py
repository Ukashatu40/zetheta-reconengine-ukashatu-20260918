# src/recon/normalisation/timestamps.py
"""Timestamp normalisation: attaches a bank's configured timezone to a
parser's naive date/datetime text, then converts to UTC (R38, A5.2).

Every format's parser produces a naive date/time string with no attached
zone — MT940's :61: value date is a bare YYMMDD, CSV dates are bare
strings in a bank-configured format, and CAMT.053's BookgDt/Dt is
typically a plain ISO date, not a DtTm with an offset. None of them know
their own timezone; the bank's configuration does. This function is
therefore the single place all three formats' dates pass through on
their way to a canonical UTC timestamp — one shared implementation, not
three format-specific ones, since the underlying operation (attach zone,
convert to UTC) is identical regardless of source format.

A5.2's own worked example ("11:30 PM IST on 15 March... recorded as 16
March in UTC+0") is arithmetically wrong as stated — 23:30 IST is 18:00
UTC the SAME day, not the next. See
docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md AE-05. This module's tests
pin the correct direction explicitly so that error is never silently
reproduced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TimestampNormalisationError(ValueError):
    """Raised when a date/time value cannot be parsed at all, or when the
    configured timezone name is not a recognised IANA zone. The latter
    should be caught earlier by BankConfig's own validator
    (recon.config.models._valid_iana_timezone) — this is a defensive
    second check, not the primary line of defence."""


@dataclass(frozen=True, slots=True)
class NormalisedTimestamp:
    utc: datetime
    original_text: str
    source_timezone: str


def normalise_timestamp(
    date_text: str,
    date_format: str,
    source_timezone: str,
    *,
    time_text: str | None = None,
    time_format: str = "%H%M",
) -> NormalisedTimestamp:
    """Parses date_text (and, optionally, time_text) as naive local values
    in source_timezone, then converts to UTC.

    time_text is optional because most of our source formats (MT940 :61:,
    CSV settlement dates, CAMT.053 BookgDt/Dt) carry only a date, no
    time-of-day. When absent, midnight local time is assumed — this is a
    deliberate, documented approximation: a transaction dated "15 March"
    with no time component is treated as having occurred at the start of
    that day in the bank's zone, which is sufficient for date-level
    reconciliation matching (T+0/T+1/T+N comparisons) but should not be
    read as claiming knowledge of an exact intraday moment.
    """
    try:
        zone = ZoneInfo(source_timezone)
    except ZoneInfoNotFoundError as exc:
        raise TimestampNormalisationError(
            f"{source_timezone!r} is not a known IANA timezone"
        ) from exc

    try:
        parsed_date = datetime.strptime(date_text.strip(), date_format).replace(tzinfo=UTC).date()
    except ValueError as exc:
        raise TimestampNormalisationError(
            f"{date_text!r} does not match expected format {date_format!r}"
        ) from exc

    if time_text is not None and time_text.strip():
        try:
            parsed_time = (
                datetime.strptime(time_text.strip(), time_format).replace(tzinfo=UTC).time()
            )
        except ValueError as exc:
            raise TimestampNormalisationError(
                f"{time_text!r} does not match expected time format {time_format!r}"
            ) from exc
        naive_local = datetime.combine(parsed_date, parsed_time)
    else:
        naive_local = datetime.combine(parsed_date, datetime.min.time())

    aware_local = naive_local.replace(tzinfo=zone)
    utc_datetime = aware_local.astimezone(UTC)

    original = date_text if time_text is None else f"{date_text} {time_text}"
    return NormalisedTimestamp(
        utc=utc_datetime, original_text=original, source_timezone=source_timezone
    )


def normalise_mt940_date(yymmdd: str, source_timezone: str) -> NormalisedTimestamp:
    """MT940's :61: value/entry dates are always YYMMDD — a thin,
    format-specific wrapper over normalise_timestamp so callers in the
    MT940 normalisation path don't need to know the exact strptime
    pattern."""
    return normalise_timestamp(yymmdd, "%y%m%d", source_timezone)


def to_utc_date(normalised: NormalisedTimestamp) -> date:
    """Convenience accessor: the calendar date (in UTC) a normalised
    timestamp falls on — this is what CanonicalTransaction.txn_date
    ultimately needs, as distinct from the full UTC datetime."""
    return normalised.utc.date()
