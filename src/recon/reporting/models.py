# src/recon/reporting/models.py
"""The shape every report shares: a title, the parameters it was run with, a flat
summary, and any number of tables. One shape means one HTML renderer, one CSV
exporter and one JSON schema for all of them."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

Cell = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class ReportTable:
    name: str
    columns: list[str]
    rows: list[list[Cell]]


@dataclass(frozen=True, slots=True)
class Report:
    kind: str
    title: str
    generated_at: datetime
    parameters: dict[str, Any]
    summary: dict[str, Cell]
    tables: list[ReportTable] = field(default_factory=list)
    # PASS / FAIL for reports that deliver a verdict (the audit compliance report).
    verdict: str | None = None
    notes: list[str] = field(default_factory=list)
