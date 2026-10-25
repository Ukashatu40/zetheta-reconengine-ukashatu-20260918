# src/recon/reporting/render.py
"""Renders a Report as self-contained HTML (printable to PDF from any browser) or
as CSV for audit tools (A7.2: exportable reports).

Every value is escaped, so a counterparty name or narration cannot inject markup.
CSV cells that start with a formula character are prefixed with an apostrophe so a
spreadsheet does not execute them."""

from __future__ import annotations

import csv
import io
from html import escape

from recon.reporting.models import Cell, Report, ReportTable

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_STYLE = (
    "body{font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;margin:2rem auto;"
    "max-width:960px;color:#1a1a1a;padding:0 1rem}"
    "h1{font-size:1.5rem;margin-bottom:.2rem}h2{font-size:1.1rem;margin-top:2rem}"
    ".meta{color:#555;font-size:.85rem}"
    "dl{display:grid;grid-template-columns:max-content 1fr;gap:.25rem 1.5rem}"
    "dt{color:#555}dd{margin:0;font-weight:600}"
    "table{border-collapse:collapse;width:100%;margin-top:.5rem}"
    "th,td{border:1px solid #d0d0d0;padding:.35rem .6rem;text-align:left}"
    "th{background:#f3f3f3}td.num{text-align:right;font-variant-numeric:tabular-nums}"
    ".pass{color:#0a6b2d}.fail{color:#b00020}.verdict{font-size:1.2rem;font-weight:700}"
    "@media print{body{margin:0}}"
)


def _text(value: Cell) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _html_cell(value: Cell) -> str:
    numeric = isinstance(value, int | float) and not isinstance(value, bool)
    return f'<td class="{"num" if numeric else ""}">{escape(_text(value))}</td>'


def _html_table(table: ReportTable) -> str:
    head = "".join(f"<th>{escape(c)}</th>" for c in table.columns)
    body = ["<tr>" + "".join(_html_cell(v) for v in row) + "</tr>" for row in table.rows]
    rows = "".join(body) or f'<tr><td colspan="{len(table.columns)}">no rows</td></tr>'
    return (
        f"<h2>{escape(table.name)}</h2><table><thead><tr>{head}</tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def render_html(report: Report) -> str:
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        f"<title>{escape(report.title)}</title><style>{_STYLE}</style></head><body>",
        f"<h1>{escape(report.title)}</h1>",
        f'<p class="meta">Generated {escape(report.generated_at.isoformat())} · '
        + escape(", ".join(f"{k}: {_text(v)}" for k, v in report.parameters.items()))
        + "</p>",
    ]
    if report.verdict:
        css = "pass" if report.verdict == "PASS" else "fail"
        parts.append(f'<p class="verdict {css}">Result: {escape(report.verdict)}</p>')
    if report.summary:
        items = "".join(
            f"<dt>{escape(k)}</dt><dd>{escape(_text(v))}</dd>" for k, v in report.summary.items()
        )
        parts.append(f"<h2>Summary</h2><dl>{items}</dl>")
    parts.extend(_html_table(t) for t in report.tables)
    if report.notes:
        notes = "".join(f"<li>{escape(n)}</li>" for n in report.notes)
        parts.append(f"<h2>Notes</h2><ul>{notes}</ul>")
    parts.append("</body></html>")
    return "".join(parts)


def _safe_cell(value: Cell) -> str:
    text = _text(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def render_csv(report: Report) -> str:
    """The summary first, then each table, separated by a blank row and a `# name` row."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["# " + report.title])
    if report.verdict:
        writer.writerow(["result", report.verdict])
    for key, value in report.summary.items():
        writer.writerow([_safe_cell(key), _safe_cell(value)])
    for table in report.tables:
        writer.writerow([])
        writer.writerow(["# " + table.name])
        writer.writerow([_safe_cell(c) for c in table.columns])
        for row in table.rows:
            writer.writerow([_safe_cell(v) for v in row])
    return out.getvalue()


def format_inr(minor: int | None) -> str:
    """Paise as rupees with Indian digit grouping: 123456789 -> 12,34,567.89."""
    if minor is None:
        return "n/a"
    sign = "-" if minor < 0 else ""
    rupees, paise = divmod(abs(minor), 100)
    digits = str(rupees)
    if len(digits) > 3:  # noqa: PLR2004
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:  # noqa: PLR2004
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    return f"{sign}₹{digits}.{paise:02d}"
