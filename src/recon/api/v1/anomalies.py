# src/recon/api/v1/anomalies.py
"""Alerts raised by the anomaly rules and the operational checks (Case Study C2,
A7.1 clause 9.1): list, acknowledge, and run the absence check on demand."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import ColumnElement, func, select

from recon.anomaly.service import AnomalyService
from recon.api.auth import Principal, require_analyst, require_viewer
from recon.api.deps import NowDep, SessionDep
from recon.api.v1.runs import get_run_settings
from recon.api.v1.schemas.responses import (
    AbsenceScanOut,
    AcknowledgeRequest,
    AlertOut,
    Page,
)
from recon.audit.chains import ALERTS_CHAIN
from recon.audit.logger import AuditLogger
from recon.domain.enums import AuditActionType
from recon.persistence.models import Alert
from recon.runs.settings import RunSettings

router = APIRouter(prefix="/api/v1/anomalies", tags=["anomalies"])


@router.get("", response_model=Page[AlertOut], dependencies=[Depends(require_viewer)])
def list_alerts(
    session: SessionDep,
    *,
    alert_type: Annotated[str | None, Query(max_length=40)] = None,
    severity: Annotated[str | None, Query(pattern="^(CRITICAL|HIGH|MEDIUM|LOW)$")] = None,
    status: Annotated[str | None, Query(pattern="^(OPEN|ACKNOWLEDGED)$")] = None,
    bank_code: Annotated[str | None, Query(max_length=20)] = None,
    route_to: Annotated[str | None, Query(pattern="^(operations|compliance)$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AlertOut]:
    filters: list[ColumnElement[bool]] = []
    for column, value in (
        (Alert.alert_type, alert_type),
        (Alert.severity, severity),
        (Alert.status, status),
        (Alert.bank_code, bank_code),
        (Alert.route_to, route_to),
    ):
        if value is not None:
            filters.append(column == value)
    total = session.scalar(select(func.count()).select_from(Alert).where(*filters)) or 0
    rows = session.scalars(
        select(Alert)
        .where(*filters)
        .order_by(Alert.created_at.desc(), Alert.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[AlertOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.put("/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge_alert(
    alert_id: uuid.UUID,
    body: AcknowledgeRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_analyst)],
    now: NowDep,
) -> AlertOut:
    row = session.get(Alert, alert_id, with_for_update=True)
    if row is None:
        raise HTTPException(status_code=404, detail="alert not found")
    if row.status == "ACKNOWLEDGED":
        raise HTTPException(status_code=409, detail="alert is already acknowledged")
    row.status = "ACKNOWLEDGED"
    row.acknowledged_at = now
    row.acknowledged_by = principal.name
    AuditLogger(session).append(
        chain_id=ALERTS_CHAIN,
        actor_type="USER",
        actor_id=f"api-key:{principal.name}",
        action_type=AuditActionType.RESOLVE,
        occurred_at=now,
        affected_records={"alert_id": str(row.id), "alert_type": row.alert_type},
        before_state={"status": "OPEN"},
        after_state={"status": "ACKNOWLEDGED"},
        rationale=body.note or "acknowledged",
    )
    session.flush()
    result = AlertOut.model_validate(row)
    session.commit()
    return result


@router.post("/scan", response_model=AbsenceScanOut)
def scan_for_absence(
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_analyst)],
    settings: Annotated[RunSettings, Depends(get_run_settings)],
    now: NowDep,
) -> AbsenceScanOut:
    """The Absence Alert: any bank past its expected settlement time with no file."""
    raised = AnomalyService(session, settings.anomaly, settings.banks).check_absence(now)
    session.commit()
    return AbsenceScanOut(alerts_raised=sum(raised.values()))
