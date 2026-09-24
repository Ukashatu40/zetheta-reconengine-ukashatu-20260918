# tests/integration/test_audit_log_append_only.py
"""Integration tests proving the audit trail's append-only guarantee.

The critical claim under test: the BEFORE UPDATE/DELETE trigger on
audit.audit_log blocks mutation for every role, including recon_app,
which owns the table and would otherwise bypass GRANT/REVOKE entirely.
See the core-persistence migration's docstring for the full reasoning.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from recon.persistence.models import AuditLog


def _make_entry(**overrides: object) -> AuditLog:
    defaults: dict[str, object] = {
        "chain_id": "system",
        "sequence_no": 1,
        "occurred_at": datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC),
        "actor_type": "SYSTEM",
        "actor_id": "ingestion-service",
        "action_type": "INGEST",
        "affected_records": {"ingestion_file_id": str(uuid.uuid4())},
        "rationale": "test fixture",
        "current_hash": "0" * 64,
        "serialisation_version": 1,
    }
    defaults.update(overrides)
    return AuditLog(**defaults)


def test_audit_entry_persists_and_reads_back(db_session: Session) -> None:
    entry = _make_entry()
    db_session.add(entry)
    db_session.flush()

    fetched = db_session.get(AuditLog, entry.event_id)
    assert fetched is not None
    assert fetched.action_type == "INGEST"
    assert fetched.recorded_at is not None


def test_update_is_rejected_even_for_the_table_owner(db_session: Session) -> None:
    """recon_app OWNS this table, so GRANT/REVOKE alone would not stop it
    from mutating a row. This proves the trigger — not just the privilege
    grants — is what actually enforces append-only."""
    entry = _make_entry(chain_id="update-test", current_hash="1" * 64)
    db_session.add(entry)
    db_session.flush()

    entry.rationale = "attempted tamper"
    with pytest.raises(ProgrammingError, match=r"append-only"):
        db_session.flush()


def test_delete_is_rejected_even_for_the_table_owner(db_session: Session) -> None:
    entry = _make_entry(chain_id="delete-test", current_hash="2" * 64)
    db_session.add(entry)
    db_session.flush()

    db_session.delete(entry)
    with pytest.raises(ProgrammingError, match=r"append-only"):
        db_session.flush()


def test_duplicate_sequence_number_within_a_chain_is_rejected(db_session: Session) -> None:
    """Chain integrity precondition: two entries cannot share
    (chain_id, sequence_no) — needed for total ordering once WP4's
    AuditLogger assigns sequence numbers."""
    db_session.add(_make_entry(chain_id="dup-test", sequence_no=1, current_hash="3" * 64))
    db_session.flush()

    db_session.add(_make_entry(chain_id="dup-test", sequence_no=1, current_hash="4" * 64))
    with pytest.raises(IntegrityError, match=r"chain_id_sequence_no|unique"):
        db_session.flush()


def test_duplicate_current_hash_is_rejected(db_session: Session) -> None:
    db_session.add(_make_entry(chain_id="hash-a", sequence_no=1, current_hash="5" * 64))
    db_session.flush()

    db_session.add(_make_entry(chain_id="hash-b", sequence_no=1, current_hash="5" * 64))
    with pytest.raises(IntegrityError, match=r"current_hash|unique"):
        db_session.flush()


def test_invalid_actor_type_is_rejected(db_session: Session) -> None:
    bad = _make_entry(chain_id="actor-test", current_hash="6" * 64, actor_type="ROBOT")
    db_session.add(bad)
    with pytest.raises(IntegrityError, match=r"actor_type_valid|check"):
        db_session.flush()
