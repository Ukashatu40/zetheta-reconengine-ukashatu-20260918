# tests/unit/security/test_role_requirement.py
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from recon.api.auth import Principal, RoleRequirement
from recon.security.rbac import Role

_ALLOWED = {
    (Role.VIEWER, Role.VIEWER),
    (Role.ANALYST, Role.VIEWER),
    (Role.ADMIN, Role.VIEWER),
    (Role.SYSTEM, Role.VIEWER),
    (Role.ANALYST, Role.ANALYST),
    (Role.ADMIN, Role.ANALYST),
    (Role.SYSTEM, Role.ANALYST),
    (Role.ADMIN, Role.ADMIN),
    (Role.SYSTEM, Role.ADMIN),
}  # fmt: skip


@pytest.mark.parametrize("required", [Role.VIEWER, Role.ANALYST, Role.ADMIN])
@pytest.mark.parametrize("actual", list(Role))
def test_role_requirement_allows_only_equal_or_higher_roles(actual: Role, required: Role) -> None:
    principal = Principal(key_id=uuid.uuid4(), name="t", role=actual)
    gate = RoleRequirement(required)

    if (actual, required) in _ALLOWED:
        assert gate(principal) is principal
    else:
        with pytest.raises(HTTPException) as caught:
            gate(principal)
        assert caught.value.status_code == 403
