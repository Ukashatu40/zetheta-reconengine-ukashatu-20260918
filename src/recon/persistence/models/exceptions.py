# src/recon/persistence/models/exceptions.py
"""ORM models for exceptions and exception_events (R63).

dedupe_key makes classification idempotent: the same unmatched record or
pair can only ever produce one exception row. A reopened exception reuses
its row (status REOPENED) rather than creating a second one.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.domain.enums import ExceptionCategory, ExceptionSeverity, ExceptionStatus
from recon.persistence.models.base import Base


def _in_list(column: str, values: Iterable[StrEnum]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v.value}'" for v in values) + ")"


EVENT_TYPES = ("CREATED", "ESCALATED", "SLA_BREACHED", "ASSIGNED", "RESOLVED", "REOPENED")
_OPEN_STATUSES_SQL = "status IN ('OPEN', 'IN_REVIEW', 'ESCALATED', 'REOPENED')"


class ReconException(Base):
    __tablename__ = "exceptions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="OPEN")
    assigned_tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank_code: Mapped[str] = mapped_column(String(20), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ingestion_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    affected_records: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    financial_impact_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    financial_impact_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    financial_impact_inr_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    suggested_resolution: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    sla_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sla_breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution_details: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_exceptions_dedupe_key"),
        CheckConstraint(_in_list("category", ExceptionCategory), name="category_valid"),
        CheckConstraint(_in_list("severity", ExceptionSeverity), name="severity_valid"),
        CheckConstraint(_in_list("status", ExceptionStatus), name="status_valid"),
        CheckConstraint("assigned_tier BETWEEN 1 AND 4", name="tier_valid"),
        # R92's literal partial index on the open queue.
        Index(
            "ix_exceptions_open_sla",
            "status",
            "sla_deadline",
            postgresql_where=text("status = 'OPEN'"),
        ),
        # What the SLA scanner actually reads: unbreached, still-open exceptions by deadline.
        Index(
            "ix_exceptions_sla_scan",
            "sla_deadline",
            postgresql_where=text(f"sla_breached_at IS NULL AND {_OPEN_STATUSES_SQL}"),
        ),
    )


class ExceptionEvent(Base):
    """Lifecycle history for an exception. These rows are also the
    simulated notification events R66 asks for."""

    __tablename__ = "exception_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    exception_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("exceptions.id", name="fk_exception_events_exception_id"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    from_tier: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    to_tier: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "event_type IN (" + ", ".join(f"'{e}'" for e in EVENT_TYPES) + ")",
            name="event_type_valid",
        ),
        Index("ix_exception_events_exception_id_occurred_at", "exception_id", "occurred_at"),
    )
