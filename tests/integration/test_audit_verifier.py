# tests/integration/test_audit_verifier.py
"""Tamper-detection tests for the audit chain.

The tests disable the append-only trigger as the table owner to simulate
someone with enough database access to bypass it. The point is that the
HASH CHAIN still detects the change; the trigger and privileges only make
it harder, they do not make the table immutable (DD-12).
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from recon.audit.canonical import compute_hash
from recon.audit.logger import AuditLogger
from recon.audit.verifier import (
    FailureKind,
    chain_head,
    load_entry_fields,
    verify_all_chains,
    verify_chain,
)
from recon.domain.enums import AuditActionType

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _build(session: Session, entries: int = 3) -> str:
    chain = f"test-{uuid.uuid4()}"
    logger = AuditLogger(session)
    for i in range(entries):
        logger.append(
            chain_id=chain, actor_type="SYSTEM", actor_id="t", action_type=AuditActionType.MATCH,
            affected_records={"i": i}, rationale=f"entry {i + 1}", occurred_at=_NOW,
        )  # fmt: skip
    return chain


def _tamper(session: Session, sql: str, **params: Any) -> None:
    session.execute(text("ALTER TABLE audit.audit_log DISABLE TRIGGER audit_log_forbid_mutation"))
    session.execute(text(sql), params)
    session.execute(text("ALTER TABLE audit.audit_log ENABLE TRIGGER audit_log_forbid_mutation"))


def test_an_untouched_chain_verifies(db_session: Session) -> None:
    chain = _build(db_session)
    result = verify_chain(db_session, chain)
    assert (result.ok, result.entries_checked) == (True, 3)


def test_an_empty_chain_verifies(db_session: Session) -> None:
    result = verify_chain(db_session, "no-such-chain")
    assert (result.ok, result.entries_checked) == (True, 0)


def test_modifying_an_entry_is_detected_at_that_entry(db_session: Session) -> None:
    chain = _build(db_session)
    _tamper(
        db_session,
        "UPDATE audit.audit_log SET rationale = 'forged' WHERE chain_id = :c AND sequence_no = 2",
        c=chain,
    )

    result = verify_chain(db_session, chain)

    assert result.ok is False
    assert (result.failure_kind, result.failure_sequence_no) == (FailureKind.HASH_MISMATCH, 2)
    assert result.entries_checked == 1


def test_deleting_a_middle_entry_is_detected_as_a_sequence_gap(db_session: Session) -> None:
    chain = _build(db_session)
    _tamper(
        db_session, "DELETE FROM audit.audit_log WHERE chain_id = :c AND sequence_no = 2", c=chain
    )

    result = verify_chain(db_session, chain)

    assert (result.failure_kind, result.failure_sequence_no) == (FailureKind.SEQUENCE_GAP, 3)


def test_an_attacker_who_recomputes_the_hash_is_caught_by_the_next_entry(
    db_session: Session,
) -> None:
    """A per-row hash alone would pass this. The chain does not."""
    chain = _build(db_session)
    forged = replace(load_entry_fields(db_session, chain, 2), rationale="forged")
    _tamper(
        db_session,
        "UPDATE audit.audit_log SET rationale = 'forged', current_hash = :h "
        "WHERE chain_id = :c AND sequence_no = 2",
        h=compute_hash(forged), c=chain,
    )  # fmt: skip

    result = verify_chain(db_session, chain)

    assert (result.failure_kind, result.failure_sequence_no) == (FailureKind.PREV_HASH_MISMATCH, 3)


def test_tail_truncation_needs_an_exported_head_to_be_detected(db_session: Session) -> None:
    """Documented limit: deleting the newest entry leaves a valid shorter
    chain. An anchor taken earlier exposes it."""
    chain = _build(db_session)
    anchor = chain_head(db_session, chain)
    assert anchor is not None and anchor.sequence_no == 3
    _tamper(
        db_session, "DELETE FROM audit.audit_log WHERE chain_id = :c AND sequence_no = 3", c=chain
    )

    assert verify_chain(db_session, chain).ok is True  # not detectable without an anchor
    anchored = verify_chain(db_session, chain, expected_head=anchor)
    assert (anchored.ok, anchored.failure_kind) == (False, FailureKind.HEAD_MISMATCH)


def test_verify_all_chains_reports_each_chain_separately(db_session: Session) -> None:
    good, bad = _build(db_session), _build(db_session)
    _tamper(
        db_session,
        "UPDATE audit.audit_log SET rationale = 'x' WHERE chain_id = :c AND sequence_no = 1",
        c=bad,
    )

    results = verify_all_chains(db_session)

    assert results[good].ok is True
    assert results[bad].failure_kind is FailureKind.HASH_MISMATCH
