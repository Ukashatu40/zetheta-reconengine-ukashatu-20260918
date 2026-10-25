# src/recon/persistence/models/statements.py
"""ORM model for statement_balances: the opening and closing balances a bank
statement states, and whether its entries add up between them (Case Study C5)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.persistence.models.base import Base


class StatementBalance(Base):
    __tablename__ = "statement_balances"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    ingestion_file_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ingestion_files.id", name="fk_statement_balances_ingestion_file_id"),
        nullable=False,
        unique=True,
    )
    bank_code: Mapped[str] = mapped_column(String(20), nullable=False)
    statement_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    account: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    opening_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    closing_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    computed_closing_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    net_entries_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    difference_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unparseable_entries: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    balanced: Mapped[bool] = mapped_column(Boolean, nullable=False)
    opening_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    closing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (CheckConstraint("entry_count >= 0", name="entry_count_non_negative"),)
