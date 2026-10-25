# src/recon/api/v1/me.py
"""Who the caller is, so a client can show only the actions the key's role allows."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from recon.api.auth import Principal, require_viewer
from recon.api.v1.schemas.responses import MeOut

router = APIRouter(prefix="/api/v1", tags=["session"])


@router.get("/me", response_model=MeOut)
def me(principal: Annotated[Principal, Depends(require_viewer)]) -> MeOut:
    return MeOut(name=principal.name, role=principal.role.value)
