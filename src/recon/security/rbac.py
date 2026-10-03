# src/recon/security/rbac.py
"""The four roles from A9.2. Hierarchical: each role includes the one below
it (ANALYST = VIEWER + more, and so on)."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    VIEWER = "VIEWER"
    ANALYST = "ANALYST"
    ADMIN = "ADMIN"
    SYSTEM = "SYSTEM"


_RANK: dict[Role, int] = {Role.VIEWER: 1, Role.ANALYST: 2, Role.ADMIN: 3, Role.SYSTEM: 4}


def has_at_least(actual: Role, required: Role) -> bool:
    return _RANK[actual] >= _RANK[required]
