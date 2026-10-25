"""Report rendering: escaping, CSV safety, Indian digit grouping."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from recon.reporting.models import Report, ReportTable
from recon.reporting.render import format_inr, render_csv, render_html

_NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)


def _report(**overrides: object) -> Report:
    values: dict[str, object] = {
        "kind": "test",
        "title": "A Report",
        "generated_at": _NOW,
        "parameters": {"date": "2026-03-16"},
        "summary": {"transactions": 12, "match_rate": 0.9, "ok": True},
        "tables": [ReportTable("Rows", ["Name", "Count"], [["alpha", 1], ["beta", 22]])],
    }
    values.update(overrides)
    return Report(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("minor", "expected"),
    [
        (0, "₹0.00"),
        (5, "₹0.05"),
        (123_456, "₹1,234.56"),
        (12_345_678, "₹1,23,456.78"),
        (1_199_999_976, "₹1,19,99,999.76"),  # B4.2's INR 1.2 crore, the way Indians write it
        (-250_000, "-₹2,500.00"),
        (100_000_000_000, "₹1,00,00,00,000.00"),
        (None, "n/a"),
    ],
)
def test_format_inr_uses_indian_digit_grouping(minor: int | None, expected: str) -> None:
    assert format_inr(minor) == expected


def test_html_contains_the_title_summary_and_tables() -> None:
    html = render_html(_report())
    assert "<title>A Report</title>" in html and "<h1>A Report</h1>" in html
    assert "<dt>transactions</dt><dd>12</dd>" in html and "<dd>yes</dd>" in html
    assert "<th>Name</th>" in html and '<td class="num">22</td>' in html
    assert "date: 2026-03-16" in html


def test_html_escapes_every_value() -> None:
    nasty = '<script>alert("x")</script>'
    html = render_html(
        _report(
            title=nasty,
            summary={nasty: nasty},
            tables=[ReportTable(nasty, [nasty], [[nasty]])],
            notes=[nasty],
        )
    )
    assert "<script>" not in html
    assert html.count("&lt;script&gt;") >= 6


def test_a_verdict_is_shown_with_its_colour_class() -> None:
    assert 'class="verdict pass"' in render_html(_report(verdict="PASS"))
    assert 'class="verdict fail"' in render_html(_report(verdict="FAIL"))
    assert "verdict" not in render_html(_report(verdict=None)).split("</style>")[1]


def test_an_empty_table_says_so() -> None:
    html = render_html(_report(tables=[ReportTable("Nothing", ["A", "B"], [])]))
    assert 'colspan="2">no rows' in html


def test_csv_has_the_summary_then_each_table() -> None:
    lines = render_csv(_report(verdict="PASS")).splitlines()
    assert lines[0] == "# A Report" and lines[1] == "result,PASS"
    assert "transactions,12" in lines and "ok,yes" in lines
    assert lines[lines.index("# Rows") + 1] == "Name,Count"
    assert "beta,22" in lines


@pytest.mark.parametrize("cell", ["=1+1", "+cmd", "-2+3", "@SUM(A1)", "\tx"])
def test_csv_cells_that_look_like_formulas_are_neutralised(cell: str) -> None:
    out = render_csv(_report(tables=[ReportTable("T", ["c"], [[cell]])]))
    assert f"'{cell}" in out or f'"\'{cell}"' in out


def test_csv_quotes_commas_and_newlines_properly() -> None:
    out = render_csv(_report(tables=[ReportTable("T", ["c"], [["a, b\nc"]])]))
    assert '"a, b\nc"' in out
