# src/recon/api/v1/audit.py
"""Audit read endpoints (VIEWER and above, per A9.2)."""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import ColumnElement, Date, cast, func, select

from recon.api.auth import require_viewer
from recon.api.deps import NowDep, SessionDep
from recon.api.v1.schemas.responses import (
    AnchorOut,
    AnchorsOut,
    AuditEntryOut,
    ChainVerificationOut,
    Page,
    VerifyOut,
)
from recon.audit.verifier import ChainHead, chain_head, verify_all_chains, verify_chain
from recon.domain.enums import AuditActionType
from recon.persistence.models import AuditLog

router = APIRouter(prefix="/api/v1/audit", tags=["audit"], dependencies=[Depends(require_viewer)])


_EXPORT_LIMIT = 50_000


def _filters(
    chain_id: str | None,
    action_type: AuditActionType | None,
    actor_id: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = []
    if chain_id is not None:
        filters.append(AuditLog.chain_id == chain_id)
    if action_type is not None:
        filters.append(AuditLog.action_type == action_type.value)
    if actor_id is not None:
        filters.append(AuditLog.actor_id == actor_id)
    occurred_day = cast(AuditLog.occurred_at, Date)
    if date_from is not None:
        filters.append(occurred_day >= date_from)
    if date_to is not None:
        filters.append(occurred_day <= date_to)
    return filters


@router.get("", response_model=Page[AuditEntryOut])
def list_audit(
    session: SessionDep,
    *,
    chain_id: Annotated[str | None, Query(max_length=100)] = None,
    action_type: AuditActionType | None = None,
    actor_id: Annotated[str | None, Query(max_length=100)] = None,
    date_from: Annotated[date | None, Query(description="Occurred on or after.")] = None,
    date_to: Annotated[date | None, Query(description="Occurred on or before.")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AuditEntryOut]:
    filters = _filters(chain_id, action_type, actor_id, date_from, date_to)

    total = session.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
    rows = session.scalars(
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.chain_id, AuditLog.sequence_no.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(
        items=[AuditEntryOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/export")
def export_audit(
    session: SessionDep,
    *,
    chain_id: Annotated[str | None, Query(max_length=100)] = None,
    action_type: AuditActionType | None = None,
    actor_id: Annotated[str | None, Query(max_length=100)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    """The audit trail as CSV for external audit tools (A7.2). At most 50,000 rows;
    narrow the filters for more."""
    filters = _filters(chain_id, action_type, actor_id, date_from, date_to)
    rows = session.scalars(
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.chain_id, AuditLog.sequence_no)
        .limit(_EXPORT_LIMIT)
    ).all()
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "chain_id",
            "sequence_no",
            "occurred_at",
            "recorded_at",
            "actor_type",
            "actor_id",
            "action_type",
            "affected_records",
            "before_state",
            "after_state",
            "rationale",
            "prev_hash",
            "current_hash",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.chain_id,
                r.sequence_no,
                r.occurred_at.isoformat(),
                r.recorded_at.isoformat(),
                r.actor_type,
                r.actor_id,
                r.action_type,
                json.dumps(r.affected_records, sort_keys=True),
                json.dumps(r.before_state, sort_keys=True) if r.before_state is not None else "",
                json.dumps(r.after_state, sort_keys=True) if r.after_state is not None else "",
                r.rationale,
                r.prev_hash or "",
                r.current_hash,
            ]
        )
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
    )


@router.get("/anchors", response_model=AnchorsOut)
def audit_anchors(session: SessionDep, now: NowDep) -> AnchorsOut:
    """The current head of every chain. Store these outside the database: passing them
    back to /verify later detects deletion of the newest entries, which a hash walk alone
    cannot (DD-12)."""
    heads = []
    for chain_id in session.scalars(
        select(AuditLog.chain_id).distinct().order_by(AuditLog.chain_id)
    ):
        head = chain_head(session, chain_id)
        if head is not None:
            heads.append(
                AnchorOut(
                    chain_id=chain_id, sequence_no=head.sequence_no, current_hash=head.current_hash
                )
            )
    return AnchorsOut(generated_at=now, anchors=heads)


def _parse_expected(values: list[str]) -> dict[str, ChainHead]:
    expected: dict[str, ChainHead] = {}
    for value in values:
        try:
            chain_id, sequence, current_hash = value.rsplit(":", 2)
            expected[chain_id] = ChainHead(int(sequence), current_hash)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"expected anchors look like chain:sequence:hash, got {value!r}",
            ) from exc
    return expected


@router.get("/verify", response_model=VerifyOut)
def verify_audit(
    session: SessionDep,
    expected: Annotated[
        list[str] | None,
        Query(description="Anchors saved earlier, as chain_id:sequence_no:hash, one per chain."),
    ] = None,
) -> VerifyOut:
    """Walks every chain, re-computing every hash. With `expected` anchors it also detects
    deletion of the newest entries; without them it cannot (DD-12)."""
    anchors = _parse_expected(expected or [])
    results = verify_all_chains(session)
    for chain_id, head in anchors.items():
        results[chain_id] = verify_chain(session, chain_id, expected_head=head)
    chains = {
        chain_id: ChainVerificationOut(
            ok=result.ok,
            entries_checked=result.entries_checked,
            failure_kind=result.failure_kind.value if result.failure_kind else None,
            failure_sequence_no=result.failure_sequence_no,
            detail=result.detail,
        )
        for chain_id, result in sorted(results.items())
    }
    return VerifyOut(ok=all(c.ok for c in chains.values()), chains=chains)
