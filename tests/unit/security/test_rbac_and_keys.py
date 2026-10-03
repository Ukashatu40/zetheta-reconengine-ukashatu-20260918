# tests/unit/security/test_rbac_and_keys.py
from __future__ import annotations

import re

import pytest

from recon.security.api_keys import generate_api_key, hash_api_key
from recon.security.rbac import Role, has_at_least


@pytest.mark.parametrize(
    ("actual", "required", "allowed"),
    [
        (Role.VIEWER, Role.VIEWER, True),
        (Role.VIEWER, Role.ANALYST, False),
        (Role.ANALYST, Role.VIEWER, True),
        (Role.ANALYST, Role.ADMIN, False),
        (Role.ADMIN, Role.ANALYST, True),
        (Role.ADMIN, Role.SYSTEM, False),
        (Role.SYSTEM, Role.ADMIN, True),
    ],
)
def test_role_hierarchy(actual: Role, required: Role, allowed: bool) -> None:
    assert has_at_least(actual, required) is allowed


def test_generated_keys_are_unique_prefixed_and_long() -> None:
    keys = {generate_api_key() for _ in range(50)}
    assert len(keys) == 50
    assert all(k.startswith("rk_") and len(k) >= 40 for k in keys)


def test_hash_is_deterministic_64_hex_and_not_the_key() -> None:
    key = generate_api_key()
    assert hash_api_key(key) == hash_api_key(key)
    assert re.fullmatch(r"[0-9a-f]{64}", hash_api_key(key))
    assert key not in hash_api_key(key)


def test_different_keys_hash_differently() -> None:
    assert hash_api_key("rk_a") != hash_api_key("rk_b")
