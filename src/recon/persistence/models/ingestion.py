# src/recon/persistence/models/ingestion.py
"""ORM models for file ingestion and raw (pre-normalisation) transactions.

Corresponds to PDF Day 1's R21/R22 and Section 8's ingestion_files /
raw_transactions tables.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.persistence.models.base import Base


class IngestionFile(Base):
    """A single uploaded/ingested source file.

    R22/B4.4: content_sha256 is unique — the database-enforced half of
    duplicate-file rejection. The ingestion service (WP2) checks this
    before accepting a file; this constraint is the backstop if it doesn't.
    """

    __tablename__ = "ingestion_files"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    bank_code: Mapped[str] = mapped_column(String(20), nullable=False)
    format_type: Mapped[str] = mapped_column(String(20), nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Nullable: CSV files rarely carry these; MT940 :28C: / CAMT statement
    # metadata populate them once those parsers land.
    statement_period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    statement_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    statement_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    ingested_by: Mapped[str] = mapped_column(String(100), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="PENDING")
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    parse_error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'PARSED', 'PARTIALLY_PARSED', 'FAILED', 'REJECTED_DUPLICATE')",
            name="status_valid",
        ),
        CheckConstraint("size_bytes >= 0", name="size_bytes_non_negative"),
        CheckConstraint("record_count >= 0", name="record_count_non_negative"),
        CheckConstraint("parse_error_count >= 0", name="parse_error_count_non_negative"),
    )


class RawTransaction(Base):
    """Immutable record of a transaction exactly as it arrived, before any
    interpretation (R21).

    Partitioned by `ingested_on` (RANGE) per R92/A6.2. The primary key must
    include the partition key — Postgres requires this — which is why `id`
    alone cannot be the sole PK here.
    """

    __tablename__ = "raw_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    ingested_on: Mapped[date] = mapped_column(Date, primary_key=True)

    ingestion_file_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("ingestion_files.id"), nullable=False
    )
    bank_code: Mapped[str] = mapped_column(String(20), nullable=False)
    format_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_line_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    parse_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="PENDING")
    parse_errors: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "parse_status IN ('PENDING', 'PARSED', 'FAILED', 'QUARANTINED')",
            name="parse_status_valid",
        ),
        Index("ix_raw_transactions_file_line", "ingestion_file_id", "source_line_no"),
        Index("ix_raw_transactions_ingested_on_brin", "ingested_on", postgresql_using="brin"),
        {"postgresql_partition_by": "RANGE (ingested_on)"},
    )
