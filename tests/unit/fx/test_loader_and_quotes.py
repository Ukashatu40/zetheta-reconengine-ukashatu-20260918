"""FX CSV parsing, outlier flagging, and quote selection."""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pytest

from recon.fx.loader import parse_fx_csv
from recon.fx.service import pick_quote
from recon.persistence.models import FxRate


def _csv(*rows: str, header: str = "date,currency,rate,source") -> io.StringIO:
    return io.StringIO("\n".join([header, *rows]) + "\n")


def test_rows_parse_with_iso_and_day_first_dates() -> None:
    result = parse_fx_csv(_csv("2026-03-15,gbp,83.47,RBI", "16-03-2026,USD,83.1,FBIL"))
    assert [(r.rate_date, r.currency, r.rate_inr, r.source) for r in result.rates] == [
        (date(2026, 3, 15), "GBP", Decimal("83.47"), "RBI"),
        (date(2026, 3, 16), "USD", Decimal("83.1"), "FBIL"),
    ]
    assert result.errors == []


def test_source_defaults_to_rbi_and_rate_inr_is_an_accepted_header() -> None:
    result = parse_fx_csv(_csv("2026-03-15,GBP,83.47", header="date,currency,rate_inr"))
    assert result.rates[0].source == "RBI"


def test_bad_rows_are_reported_with_their_line_number_and_do_not_stop_the_file() -> None:
    result = parse_fx_csv(
        _csv(
            "2026-03-15,GBP,83.47,RBI",
            "2026-03-15,GB,83.47,RBI",
            "2026-03-15,USD,abc,RBI",
            "2026-03-15,USD,-1,RBI",
            "yesterday,USD,83,RBI",
            "2026-03-15,USD,83,ECB",
        )
    )
    assert len(result.rates) == 1
    assert [e.line_no for e in result.errors] == [3, 4, 5, 6, 7]
    assert "invalid currency" in result.errors[0].detail
    assert "unknown source" in result.errors[-1].detail


def test_missing_columns_are_rejected() -> None:
    with pytest.raises(ValueError, match="needs the columns"):
        parse_fx_csv(_csv("2026-03-15,83", header="date,rate"))


def test_a_wrong_rate_is_flagged_against_the_file_median() -> None:
    rows = [f"2026-03-{d:02d},GBP,83.{d:02d},RBI" for d in range(1, 11)]
    rows.append("2026-03-11,GBP,98.50,RBI")  # planted
    result = parse_fx_csv(_csv(*rows))
    assert [r.rate_inr for r in result.suspects] == [Decimal("98.50")]
    assert "from the file median" in (result.suspects[0].suspect_reason or "")


def test_a_currency_with_too_few_points_is_never_flagged() -> None:
    result = parse_fx_csv(_csv("2026-03-01,GBP,83,RBI", "2026-03-02,GBP,200,RBI"))
    assert result.suspects == []


def test_the_median_is_per_currency_and_source() -> None:
    result = parse_fx_csv(
        _csv(
            *(f"2026-03-0{d},GBP,100,RBI" for d in range(1, 5)),
            *(f"2026-03-0{d},USD,50,RBI" for d in range(1, 5)),
        )
    )
    assert result.suspects == []


def _row(day: int, source: str = "RBI", rate: str = "83", *, suspect: bool = False) -> FxRate:
    return FxRate(
        currency="GBP",
        rate_inr=Decimal(rate),
        source=source,
        rate_date=date(2026, 3, day),
        suspect=suspect,
    )


def test_the_same_day_rate_wins_and_reports_no_staleness() -> None:
    quote = pick_quote([_row(14, rate="82"), _row(15, rate="83")], date(2026, 3, 15), 3)
    assert quote is not None
    assert (quote.rate_inr, quote.days_stale) == (Decimal("83"), 0)


def test_the_source_hierarchy_breaks_a_tie() -> None:
    quote = pick_quote(
        [_row(15, "BANK_CARD", "84"), _row(15, "FBIL", "83.9"), _row(15, "RBI", "83.5")],
        date(2026, 3, 15),
        3,
    )
    assert quote is not None
    assert (quote.source, quote.rate_inr) == ("RBI", Decimal("83.5"))


def test_an_older_rate_is_used_within_the_staleness_window_and_says_so() -> None:
    quote = pick_quote([_row(13)], date(2026, 3, 15), 3)
    assert quote is not None
    assert quote.days_stale == 2
    assert pick_quote([_row(10)], date(2026, 3, 15), 3) is None


def test_a_future_rate_is_never_used() -> None:
    assert pick_quote([_row(16)], date(2026, 3, 15), 3) is None


def test_a_clean_older_rate_beats_a_suspect_same_day_rate() -> None:
    quote = pick_quote(
        [_row(15, rate="98", suspect=True), _row(14, rate="83")], date(2026, 3, 15), 3
    )
    assert quote is not None
    assert (quote.rate_inr, quote.suspect) == (Decimal("83"), False)


def test_a_suspect_rate_is_used_when_nothing_else_exists() -> None:
    quote = pick_quote([_row(15, rate="98", suspect=True)], date(2026, 3, 15), 3)
    assert quote is not None
    assert quote.suspect is True
