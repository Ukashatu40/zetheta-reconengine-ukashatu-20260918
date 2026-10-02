# src/recon/audit/logger.py
"""AuditLogger (R68, R69): appends hash-chained entries to audit.audit_log.

Concurrency: a transaction-scoped advisory lock per chain serialises
writers, so two transactions cannot both read the same head and fork the
chain. The lock is held until the surrounding transaction ends, so keep
audit-writing transactions short. The (chain_id, sequence_no) unique
constraint is the backstop if the lock is ever bypassed.

Nothing is written if serialisation fails.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from recon.audit.canonical import SERIALISATION_VERSION, AuditEntryFields, compute_hash
from recon.domain.enums import AuditActionType
from recon.persistence.models import AuditLog

_ACTOR_TYPES = ("SYSTEM", "USER")


class AuditLogger:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        *,
        chain_id: str,
        actor_type: str,
        actor_id: str,
        action_type: AuditActionType,
        affected_records: dict[str, Any],
        rationale: str,
        occurred_at: datetime,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
    ) -> AuditLog:
        if actor_type not in _ACTOR_TYPES:
            raise ValueError(f"actor_type must be one of {_ACTOR_TYPES}, got {actor_type!r}")
        if occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")

        self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"audit:{chain_id}"},
        )
        head = self._session.execute(
            select(AuditLog.sequence_no, AuditLog.current_hash)
            .where(AuditLog.chain_id == chain_id)
            .order_by(AuditLog.sequence_no.desc())
            .limit(1)
        ).first()

        fields = AuditEntryFields(
            event_id=uuid.uuid4(),
            chain_id=chain_id,
            sequence_no=(head.sequence_no + 1) if head else 1,
            occurred_at=occurred_at,
            actor_type=actor_type,
            actor_id=actor_id,
            action_type=action_type.value,
            affected_records=affected_records,
            before_state=before_state,
            after_state=after_state,
            rationale=rationale,
            prev_hash=head.current_hash if head else None,
        )
        entry = AuditLog(
            event_id=fields.event_id,
            chain_id=fields.chain_id,
            sequence_no=fields.sequence_no,
            occurred_at=fields.occurred_at,
            actor_type=fields.actor_type,
            actor_id=fields.actor_id,
            action_type=fields.action_type,
            affected_records=fields.affected_records,
            before_state=fields.before_state,
            after_state=fields.after_state,
            rationale=fields.rationale,
            prev_hash=fields.prev_hash,
            current_hash=compute_hash(fields),  # raises before anything is written
            serialisation_version=SERIALISATION_VERSION,
        )
        self._session.add(entry)
        self._session.flush()
        return entry
