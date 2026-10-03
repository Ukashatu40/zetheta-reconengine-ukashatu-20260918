# tests/unit/api/test_route_protection.py
"""Every route under /api must require a role. A new endpoint without one
fails this test, so it cannot ship unguarded."""

from __future__ import annotations

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from recon.api.auth import RoleRequirement
from recon.api.main import app

_PUBLIC_PATHS = {"/health/live", "/health/ready"}
_ROUTES = [r for r in app.routes if isinstance(r, APIRoute)]
_PROTECTED = [r for r in _ROUTES if r.path not in _PUBLIC_PATHS]


def _requirements(dependant: Dependant) -> list[object]:
    found: list[object] = [
        d.call for d in dependant.dependencies if isinstance(d.call, RoleRequirement)
    ]
    for sub in dependant.dependencies:
        found.extend(_requirements(sub))
    return found


@pytest.mark.parametrize("route", _PROTECTED, ids=lambda r: f"{sorted(r.methods or [])} {r.path}")
def test_every_non_public_route_requires_a_role(route: APIRoute) -> None:
    assert _requirements(route.dependant), f"{route.path} has no role requirement"


@pytest.mark.parametrize("route", _PROTECTED, ids=lambda r: r.path)
def test_every_protected_route_is_versioned(route: APIRoute) -> None:
    assert route.path.startswith("/api/v1/")


def test_the_public_routes_are_exactly_the_health_checks() -> None:
    assert {r.path for r in _ROUTES} - {r.path for r in _PROTECTED} == _PUBLIC_PATHS


# def test_the_check_is_not_vacuous() -> None:
#     assert len(_PROTECTED) >= 6
