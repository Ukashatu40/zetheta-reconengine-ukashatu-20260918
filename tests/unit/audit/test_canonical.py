# tests/unit/audit/test_canonical.py
from __future__ import annotations

import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from recon.audit.canonical import (
    AuditEntryFields,
    CanonicalisationError,
    canonical_bytes,
    compute_hash,
)


def _base() -> AuditEntryFields:
    return AuditEntryFields(
        event_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        chain_id="c",
        sequence_no=1,
        occurred_at=datetime(2026, 3, 15, 12, 0, 0, 123456, tzinfo=UTC),
        actor_type="SYSTEM",
        actor_id="a",
        action_type="INGEST",
        affected_records={"b": 2, "a": 1},
        before_state=None,
        after_state={"k": "é"},
        rationale="r",
        prev_hash=None,
    )


def test_canonical_bytes_are_pinned() -> None:
    """The exact bytes for version 1. Changing this test means changing the
    serialisation version, which invalidates every stored hash."""
    expected = (
        '{"action_type":"INGEST","actor_id":"a","actor_type":"SYSTEM",'
        '"affected_records":{"a":1,"b":2},"after_state":{"k":"é"},"before_state":null,'
        '"chain_id":"c","event_id":"00000000-0000-0000-0000-000000000001",'
        '"occurred_at":"2026-03-15T12:00:00.123456Z","prev_hash":null,"rationale":"r",'
        '"seq":1,"v":1}'
    )
    assert canonical_bytes(_base()) == expected.encode("utf-8")


def test_hash_is_64_lowercase_hex() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", compute_hash(_base()))


def test_dict_key_order_does_not_change_the_hash() -> None:
    reordered = replace(_base(), affected_records={"a": 1, "b": 2})
    assert compute_hash(reordered) == compute_hash(_base())


def test_same_instant_in_another_timezone_hashes_identically() -> None:
    ist = datetime(2026, 3, 15, 17, 30, 0, 123456, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert compute_hash(replace(_base(), occurred_at=ist)) == compute_hash(_base())


_CHANGES: list[tuple[str, Any]] = [
    ("event_id", uuid.UUID("00000000-0000-0000-0000-000000000002")),
    ("chain_id", "d"),
    ("sequence_no", 2),
    ("occurred_at", datetime(2026, 3, 15, 12, 0, 0, 123457, tzinfo=UTC)),
    ("actor_type", "USER"),
    ("actor_id", "b"),
    ("action_type", "MATCH"),
    ("affected_records", {"a": 1}),
    ("before_state", {"k": 1}),
    ("after_state", {"k": "e"}),
    ("rationale", "r2"),
    ("prev_hash", "a" * 64),
]


@pytest.mark.parametrize(("name", "value"), _CHANGES, ids=[c[0] for c in _CHANGES])
def test_changing_any_field_changes_the_hash(name: str, value: Any) -> None:
    assert compute_hash(replace(_base(), **{name: value})) != compute_hash(_base())


def test_floats_are_rejected_even_when_nested() -> None:
    with pytest.raises(CanonicalisationError, match="floats"):
        canonical_bytes(replace(_base(), after_state={"x": {"y": [1.5]}}))


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(CanonicalisationError, match="timezone-aware"):
        canonical_bytes(replace(_base(), occurred_at=datetime(2026, 3, 15, 12, 0)))  # noqa: DTZ001


def test_non_string_dict_key_is_rejected() -> None:
    with pytest.raises(CanonicalisationError, match="keys must be strings"):
        canonical_bytes(replace(_base(), affected_records={1: "x"}))  # type: ignore[dict-item]


def test_unsupported_serialisation_version_is_rejected() -> None:
    with pytest.raises(CanonicalisationError, match="version"):
        canonical_bytes(replace(_base(), serialisation_version=2))


def test_lone_surrogate_is_rejected() -> None:
    with pytest.raises(CanonicalisationError, match="unicode"):
        canonical_bytes(replace(_base(), rationale="\ud800"))
