# src/recon/api/v1/audit.py
"""Audit read endpoints (VIEWER and above, per A9.2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import ColumnElement, func, select

from recon.api.auth import require_viewer
from recon.api.deps import SessionDep
from recon.api.v1.schemas.responses import AuditEntryOut, ChainVerificationOut, Page, VerifyOut
from recon.audit.verifier import verify_all_chains
from recon.domain.enums import AuditActionType
from recon.persistence.models import AuditLog

router = APIRouter(prefix="/api/v1/audit", tags=["audit"], dependencies=[Depends(require_viewer)])


@router.get("", response_model=Page[AuditEntryOut])
def list_audit(
    session: SessionDep,
    chain_id: Annotated[str | None, Query(max_length=100)] = None,
    action_type: AuditActionType | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AuditEntryOut]:
    filters: list[ColumnElement[bool]] = []
    if chain_id is not None:
        filters.append(AuditLog.chain_id == chain_id)
    if action_type is not None:
        filters.append(AuditLog.action_type == action_type.value)

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


@router.get("/verify", response_model=VerifyOut)
def verify_audit(session: SessionDep) -> VerifyOut:
    """Walks every chain. Cannot detect deletion of the newest entries,
    because no anchor is exported yet (DD-12)."""
    chains = {
        chain_id: ChainVerificationOut(
            ok=result.ok,
            entries_checked=result.entries_checked,
            failure_kind=result.failure_kind.value if result.failure_kind else None,
            failure_sequence_no=result.failure_sequence_no,
            detail=result.detail,
        )
        for chain_id, result in verify_all_chains(session).items()
    }
    return VerifyOut(ok=all(c.ok for c in chains.values()), chains=chains)
