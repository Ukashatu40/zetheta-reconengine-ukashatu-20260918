# src/recon/persistence/models/audit.py
"""ORM model for the append-only audit trail.

Mirrors audit.audit_log as created by the core-persistence migration. See
that migration's module docstring for the full reasoning on why this table
is genuinely append-only: REVOKE restricts recon_audit; a BEFORE
UPDATE/DELETE trigger restricts everyone else, including the table owner.

This model is written to exclusively through recon.audit.logger (WP4)
using a session bound to a recon_audit-authenticated engine, never through
ad-hoc session.add()/commit() calls elsewhere in the codebase.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.persistence.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        UniqueConstraint("chain_id", "sequence_no", name="uq_audit_log_chain_id_sequence_no"),
        CheckConstraint("actor_type IN ('SYSTEM', 'USER')", name="actor_type_valid"),
        {"schema": "audit"},
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    chain_id: Mapped[str] = mapped_column(String(100), nullable=False)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    affected_records: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    serialisation_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
