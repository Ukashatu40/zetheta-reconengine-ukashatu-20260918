# src/recon/persistence/models/runs.py
"""ORM model for reconciliation_runs (A6.2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.persistence.models.base import Base

RUN_STATUSES = ("RUNNING", "COMPLETED", "FAILED")


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    bank_code: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="RUNNING")
    requested_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_reconciliation_runs_idempotency_key"),
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in RUN_STATUSES) + ")", name="status_valid"
        ),
        # The concurrency guard: at most one RUNNING run per bank.
        Index(
            "ux_reconciliation_runs_one_running_per_bank",
            "bank_code",
            unique=True,
            postgresql_where=text("status = 'RUNNING'"),
        ),
        Index("ix_reconciliation_runs_created_at", "created_at"),
    )
