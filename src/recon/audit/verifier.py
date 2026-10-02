# src/recon/audit/verifier.py
"""Audit chain verification (R70).

Checks, per entry in sequence order: contiguous sequence numbers, the
stored prev_hash equals the previous entry's current_hash, and the
recomputed hash equals the stored one. Reports the FIRST failure.

Reads columns, never ORM entities: an entity already in the session's
identity map could hold stale values and hide a change made in the
database.

Known limit: deleting the NEWEST entries leaves a valid shorter chain.
Detecting that needs a head exported outside the table: chain_head()
returns one, and verify_chain(expected_head=...) checks it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.audit.canonical import (
    SERIALISATION_VERSION,
    AuditEntryFields,
    CanonicalisationError,
    compute_hash,
)
from recon.persistence.models import AuditLog

_BATCH = 1000


class FailureKind(StrEnum):
    SEQUENCE_GAP = "SEQUENCE_GAP"
    PREV_HASH_MISMATCH = "PREV_HASH_MISMATCH"
    HASH_MISMATCH = "HASH_MISMATCH"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    HEAD_MISMATCH = "HEAD_MISMATCH"


@dataclass(frozen=True, slots=True)
class ChainHead:
    sequence_no: int
    current_hash: str


@dataclass(frozen=True, slots=True)
class VerificationResult:
    ok: bool
    entries_checked: int
    failure_kind: FailureKind | None = None
    failure_sequence_no: int | None = None
    detail: str = ""


def _columns() -> tuple[object, ...]:
    return (
        AuditLog.event_id, AuditLog.chain_id, AuditLog.sequence_no, AuditLog.occurred_at,
        AuditLog.actor_type, AuditLog.actor_id, AuditLog.action_type, AuditLog.affected_records,
        AuditLog.before_state, AuditLog.after_state, AuditLog.rationale, AuditLog.prev_hash,
        AuditLog.current_hash, AuditLog.serialisation_version,
    )  # fmt: skip


def _fields(row: object) -> AuditEntryFields:
    return AuditEntryFields(
        event_id=row.event_id,  # type: ignore[attr-defined]
        chain_id=row.chain_id,  # type: ignore[attr-defined]
        sequence_no=row.sequence_no,  # type: ignore[attr-defined]
        occurred_at=row.occurred_at,  # type: ignore[attr-defined]
        actor_type=row.actor_type,  # type: ignore[attr-defined]
        actor_id=row.actor_id,  # type: ignore[attr-defined]
        action_type=row.action_type,  # type: ignore[attr-defined]
        affected_records=row.affected_records,  # type: ignore[attr-defined]
        before_state=row.before_state,  # type: ignore[attr-defined]
        after_state=row.after_state,  # type: ignore[attr-defined]
        rationale=row.rationale,  # type: ignore[attr-defined]
        prev_hash=row.prev_hash,  # type: ignore[attr-defined]
        serialisation_version=row.serialisation_version,  # type: ignore[attr-defined]
    )


def load_entry_fields(session: Session, chain_id: str, sequence_no: int) -> AuditEntryFields:
    row = session.execute(
        select(*_columns()).where(  # type: ignore[call-overload]
            AuditLog.chain_id == chain_id, AuditLog.sequence_no == sequence_no
        )
    ).one()
    return _fields(row)


def chain_head(session: Session, chain_id: str) -> ChainHead | None:
    row = session.execute(
        select(AuditLog.sequence_no, AuditLog.current_hash)
        .where(AuditLog.chain_id == chain_id)
        .order_by(AuditLog.sequence_no.desc())
        .limit(1)
    ).first()
    return ChainHead(row.sequence_no, row.current_hash) if row else None


def verify_chain(
    session: Session, chain_id: str, *, expected_head: ChainHead | None = None
) -> VerificationResult:
    stmt = (
        select(*_columns())  # type: ignore[call-overload]
        .where(AuditLog.chain_id == chain_id)
        .order_by(AuditLog.sequence_no)
        .execution_options(yield_per=_BATCH)
    )
    expected_seq = 1
    previous_hash: str | None = None
    checked = 0

    for row in session.execute(stmt):
        failure = _check_row(row, expected_seq, previous_hash)
        if failure is not None:
            kind, detail = failure
            return VerificationResult(False, checked, kind, row.sequence_no, detail)
        previous_hash = row.current_hash
        expected_seq += 1
        checked += 1

    if expected_head is not None:
        actual = ChainHead(expected_seq - 1, previous_hash) if previous_hash else None
        if actual != expected_head:
            return VerificationResult(
                False, checked, FailureKind.HEAD_MISMATCH, None,
                f"chain head is {actual}, expected {expected_head}",
            )  # fmt: skip
    return VerificationResult(True, checked)


def _check_row(
    row: object, expected_seq: int, previous_hash: str | None
) -> tuple[FailureKind, str] | None:
    seq = row.sequence_no  # type: ignore[attr-defined]
    if seq != expected_seq:
        return FailureKind.SEQUENCE_GAP, f"expected sequence {expected_seq}, found {seq}"
    if row.serialisation_version != SERIALISATION_VERSION:  # type: ignore[attr-defined]
        return FailureKind.UNSUPPORTED_VERSION, f"version {row.serialisation_version}"  # type: ignore[attr-defined]
    if row.prev_hash != previous_hash:  # type: ignore[attr-defined]
        return FailureKind.PREV_HASH_MISMATCH, "prev_hash does not match the previous entry"
    try:
        recomputed = compute_hash(_fields(row))
    except CanonicalisationError as exc:
        return FailureKind.HASH_MISMATCH, f"entry cannot be re-serialised: {exc}"
    if recomputed != row.current_hash:  # type: ignore[attr-defined]
        return FailureKind.HASH_MISMATCH, "stored hash does not match the recomputed hash"
    return None


def verify_all_chains(session: Session) -> dict[str, VerificationResult]:
    chain_ids = session.scalars(
        select(AuditLog.chain_id).distinct().order_by(AuditLog.chain_id)
    ).all()
    return {chain_id: verify_chain(session, chain_id) for chain_id in chain_ids}
