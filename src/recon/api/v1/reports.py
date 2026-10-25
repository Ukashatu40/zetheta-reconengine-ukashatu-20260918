# src/recon/api/v1/reports.py
"""Compliance and operations reports (Day 6, A9.2 ANALYST: generate reports).

Each report can be had as HTML (print it to PDF from any browser), CSV for audit
tools, or JSON for the dashboard (?format=html|csv|json)."""

from __future__ import annotations

from datetime import UTC, date, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse

from recon.api.auth import require_analyst
from recon.api.deps import NowDep, SessionDep
from recon.api.v1.runs import get_run_settings
from recon.api.v1.schemas.responses import ReportOut, ReportTableOut
from recon.reporting.generator import ReportGenerator
from recon.reporting.models import Report
from recon.reporting.render import render_csv, render_html
from recon.runs.settings import RunSettings

router = APIRouter(
    prefix="/api/v1/reports", tags=["reports"], dependencies=[Depends(require_analyst)]
)

ReportFormat = Annotated[
    Literal["json", "html", "csv"], Query(alias="format", description="Output format.")
]
_MAX_RANGE_DAYS = 366
_REPORT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {
            "application/json": {},
            "text/html": {},
            "text/csv": {},
        },
        "description": "The report in the requested format.",
    }
}


def _out(report: Report) -> ReportOut:
    return ReportOut(
        kind=report.kind,
        title=report.title,
        generated_at=report.generated_at,
        parameters=report.parameters,
        summary=report.summary,
        tables=[ReportTableOut(name=t.name, columns=t.columns, rows=t.rows) for t in report.tables],
        verdict=report.verdict,
        notes=report.notes,
    )


def _respond(report: Report, fmt: str) -> Response:
    if fmt == "html":
        return HTMLResponse(render_html(report))
    if fmt == "csv":
        return Response(
            render_csv(report),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{report.kind}.csv"'},
        )
    return JSONResponse(_out(report).model_dump(mode="json"))


def _generator(session: SessionDep, settings: RunSettings, now: NowDep) -> ReportGenerator:
    return ReportGenerator(session, settings.banks, now)


@router.get("/daily", response_model=None, responses=_REPORT_RESPONSES)
def daily_report(
    session: SessionDep,
    now: NowDep,
    settings: Annotated[RunSettings, Depends(get_run_settings)],
    fmt: ReportFormat = "json",
    day: Annotated[date | None, Query(alias="date", description="Default today (UTC).")] = None,
) -> Response:
    report = _generator(session, settings, now).daily(day or now.astimezone(UTC).date())
    return _respond(report, fmt)


@router.get("/exception-ageing", response_model=None, responses=_REPORT_RESPONSES)
def exception_ageing_report(
    session: SessionDep,
    now: NowDep,
    settings: Annotated[RunSettings, Depends(get_run_settings)],
    fmt: ReportFormat = "json",
) -> Response:
    return _respond(_generator(session, settings, now).exception_ageing(), fmt)


@router.get("/audit-compliance", response_model=None, responses=_REPORT_RESPONSES)
def audit_compliance_report(
    session: SessionDep,
    now: NowDep,
    settings: Annotated[RunSettings, Depends(get_run_settings)],
    *,
    fmt: ReportFormat = "json",
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    today = now.astimezone(UTC).date()
    end = date_to or today
    start = date_from or end
    if start > end:
        raise HTTPException(status_code=422, detail="date_from must not be after date_to")
    if end - start > timedelta(days=_MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=422, detail=f"the period is limited to {_MAX_RANGE_DAYS} days"
        )
    return _respond(_generator(session, settings, now).audit_compliance(start, end), fmt)


@router.get("/recovery", response_model=None, responses=_REPORT_RESPONSES)
def recovery_report(
    session: SessionDep,
    now: NowDep,
    settings: Annotated[RunSettings, Depends(get_run_settings)],
    fmt: ReportFormat = "json",
    window_hours: Annotated[int, Query(ge=1, le=168)] = 24,
) -> Response:
    return _respond(_generator(session, settings, now).recovery(window_hours), fmt)
