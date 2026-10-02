# tests/integration/test_audit_logger.py
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.audit.canonical import CanonicalisationError, compute_hash
from recon.audit.logger import AuditLogger
from recon.audit.verifier import load_entry_fields
from recon.domain.enums import AuditActionType
from recon.persistence.models import AuditLog

_NOW = datetime(2026, 3, 15, 12, 0, 0, 123456, tzinfo=UTC)


def _append(logger: AuditLogger, chain: str, **overrides: object) -> AuditLog:
    kwargs: dict[str, object] = {
        "chain_id": chain,
        "actor_type": "SYSTEM",
        "actor_id": "test",
        "action_type": AuditActionType.INGEST,
        "affected_records": {"id": "1"},
        "rationale": "test",
        "occurred_at": _NOW,
    }
    kwargs.update(overrides)
    return logger.append(**kwargs)  # type: ignore[arg-type]


def _chain() -> str:
    return f"test-{uuid.uuid4()}"


def test_first_entry_starts_the_chain_and_second_links_to_it(db_session: Session) -> None:
    logger, chain = AuditLogger(db_session), _chain()

    first = _append(logger, chain)
    second = _append(logger, chain)

    assert (first.sequence_no, first.prev_hash) == (1, None)
    assert re.fullmatch(r"[0-9a-f]{64}", first.current_hash)
    assert (second.sequence_no, second.prev_hash) == (2, first.current_hash)


def test_chains_are_independent(db_session: Session) -> None:
    logger = AuditLogger(db_session)
    a, b = _chain(), _chain()

    _append(logger, a)
    _append(logger, a)
    first_in_b = _append(logger, b)

    assert (first_in_b.sequence_no, first_in_b.prev_hash) == (1, None)


def test_stored_hash_equals_a_recomputation_from_the_stored_row(db_session: Session) -> None:
    logger, chain = AuditLogger(db_session), _chain()
    entry = _append(logger, chain, after_state={"café": "é", "n": 3})

    assert compute_hash(load_entry_fields(db_session, chain, 1)) == entry.current_hash


def test_invalid_actor_type_is_rejected(db_session: Session) -> None:
    with pytest.raises(ValueError, match="actor_type"):
        _append(AuditLogger(db_session), _chain(), actor_type="ROBOT")


def test_naive_timestamp_is_rejected(db_session: Session) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _append(
            AuditLogger(db_session), _chain(), occurred_at=datetime(2026, 3, 15)  # noqa: DTZ001
        )


def test_unserialisable_entry_writes_nothing_and_does_not_burn_a_sequence_number(
    db_session: Session,
) -> None:
    logger, chain = AuditLogger(db_session), _chain()

    with pytest.raises(CanonicalisationError):
        _append(logger, chain, after_state={"x": 1.5})

    assert db_session.scalars(select(AuditLog).where(AuditLog.chain_id == chain)).all() == []
    assert _append(logger, chain).sequence_no == 1
