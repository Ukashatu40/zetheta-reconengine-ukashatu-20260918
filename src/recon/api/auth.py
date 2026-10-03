# src/recon/api/auth.py
"""API-key authentication and role enforcement (A9.2, R76, R85).

Every protected route depends on a RoleRequirement. tests/unit/api/
test_route_protection.py walks the route table and fails if a route under
/api has none, so a new endpoint cannot ship unguarded.

Unknown, revoked and missing keys all produce the same 401 so the
response does not reveal which keys exist. The key itself is never
echoed or logged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select

from recon.api.deps import SessionDep
from recon.persistence.models import ApiKey
from recon.security.api_keys import hash_api_key
from recon.security.rbac import Role, has_at_least

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    key_id: uuid.UUID
    name: str
    role: Role


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401, detail="missing or invalid API key", headers={"WWW-Authenticate": "ApiKey"}
    )


def authenticate(
    raw_key: Annotated[str | None, Security(_api_key_header)], session: SessionDep
) -> Principal:
    if not raw_key:
        raise _unauthorized()
    row = session.execute(
        select(ApiKey.id, ApiKey.name, ApiKey.role).where(
            ApiKey.key_hash == hash_api_key(raw_key), ApiKey.revoked_at.is_(None)
        )
    ).first()
    if row is None:
        raise _unauthorized()
    return Principal(key_id=row.id, name=row.name, role=Role(row.role))


class RoleRequirement:
    def __init__(self, required: Role) -> None:
        self.required_role = required

    def __call__(self, principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        if not has_at_least(principal.role, self.required_role):
            raise HTTPException(
                status_code=403, detail=f"role {self.required_role} or higher is required"
            )
        return principal


require_viewer = RoleRequirement(Role.VIEWER)
require_analyst = RoleRequirement(Role.ANALYST)
require_admin = RoleRequirement(Role.ADMIN)
