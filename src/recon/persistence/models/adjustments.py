# src/recon/persistence/models/adjustments.py
"""ORM model for ledger_adjustments: the entry an auto-resolved FEE_DEDUCTION
creates so the internal ledger reflects a fee the bank took (A4.1 #11, Day 4)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.persistence.models.base import Base

ADJUSTMENT_TYPES = ("BANK_FEE",)


class LedgerAdjustment(Base):
    __tablename__ = "ledger_adjustments"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    exception_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("exceptions.id", name="fk_ledger_adjustments_exception_id"),
        nullable=False,
        unique=True,
    )
    bank_code: Mapped[str] = mapped_column(String(20), nullable=False)
    adjustment_type: Mapped[str] = mapped_column(String(20), nullable=False)
    internal_transaction_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    external_transaction_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint(
            "adjustment_type IN (" + ", ".join(f"'{t}'" for t in ADJUSTMENT_TYPES) + ")",
            name="type_valid",
        ),
    )
