# src/recon/audit/canonical.py
"""Canonical serialisation and hashing for audit entries (A4.3, R69, R70).

The same logical event must always produce the same bytes. Rules (version 1):
  - one JSON object, keys sorted, no whitespace, UTF-8, non-ASCII kept as is
  - occurred_at as UTC, always six microsecond digits, trailing "Z"
  - floats rejected anywhere: their text form is not stable through JSONB
  - naive datetimes and non-string dict keys rejected
  - recorded_at is excluded: the database sets it after the hash is known
  - prev_hash is included, which is what turns per-row hashes into a chain
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SERIALISATION_VERSION = 1
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class CanonicalisationError(ValueError):
    """Raised when an audit entry cannot be serialised deterministically."""


@dataclass(frozen=True, slots=True)
class AuditEntryFields:
    event_id: uuid.UUID
    chain_id: str
    sequence_no: int
    occurred_at: datetime
    actor_type: str
    actor_id: str
    action_type: str
    affected_records: dict[str, Any]
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    rationale: str
    prev_hash: str | None
    serialisation_version: int = SERIALISATION_VERSION


def _validate_json_value(value: object, path: str) -> None:
    if value is None or isinstance(value, bool | int | str):
        return
    if isinstance(value, float):
        raise CanonicalisationError(
            f"{path}: floats are not allowed in audit state; use str or int"
        )
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalisationError(f"{path}: dict keys must be strings, got {key!r}")
            _validate_json_value(item, f"{path}.{key}")
        return
    raise CanonicalisationError(f"{path}: unsupported type {type(value).__name__}")


def canonical_bytes(fields: AuditEntryFields) -> bytes:
    if fields.serialisation_version != SERIALISATION_VERSION:
        raise CanonicalisationError(
            f"unsupported serialisation version {fields.serialisation_version}"
        )
    if fields.occurred_at.tzinfo is None:
        raise CanonicalisationError("occurred_at must be timezone-aware")
    _validate_json_value(fields.affected_records, "affected_records")
    _validate_json_value(fields.before_state, "before_state")
    _validate_json_value(fields.after_state, "after_state")

    payload = {
        "v": fields.serialisation_version,
        "event_id": str(fields.event_id),
        "chain_id": fields.chain_id,
        "seq": fields.sequence_no,
        "occurred_at": fields.occurred_at.astimezone(UTC).strftime(_TIMESTAMP_FORMAT),
        "actor_type": fields.actor_type,
        "actor_id": fields.actor_id,
        "action_type": fields.action_type,
        "affected_records": fields.affected_records,
        "before_state": fields.before_state,
        "after_state": fields.after_state,
        "rationale": fields.rationale,
        "prev_hash": fields.prev_hash,
    }
    try:
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CanonicalisationError(f"text is not valid unicode: {exc}") from exc


def compute_hash(fields: AuditEntryFields) -> str:
    return hashlib.sha256(canonical_bytes(fields)).hexdigest()
