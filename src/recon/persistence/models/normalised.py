# src/recon/persistence/models/normalised.py
"""ORM model for normalised_transactions (WP1's Section 8 design, R92's
composite index requirement).

Partitioned by RANGE (txn_date) per A6.2 — matching queries filter by
date window, so partition pruning on the same column matching's blocking
strategy will key on (WP4) pays off directly, unlike raw_transactions
where the partition key (ingested_on) serves a different purpose
(retention/archival by ingestion date, not by transaction date).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.persistence.models.base import Base


class NormalisedTransaction(Base):
    __tablename__ = "normalised_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    txn_date: Mapped[date] = mapped_column(Date, primary_key=True)

    raw_transaction_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    ingestion_file_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("ingestion_files.id"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    bank_code: Mapped[str] = mapped_column(String(20), nullable=False)
    format_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_line_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    config_version: Mapped[str] = mapped_column(String(50), nullable=False)

    txn_id: Mapped[str] = mapped_column(String(200), nullable=False)
    bank_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    amount_minor: Mapped[int] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    currency_exponent: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    currency_source: Mapped[str] = mapped_column(String(30), nullable=False)
    original_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    direction: Mapped[str] = mapped_column(String(2), nullable=False)
    is_reversal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    reverses_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)

    counterparty_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    counterparty_name_normalised: Mapped[str | None] = mapped_column(Text, nullable=True)
    counterparty_account: Mapped[str | None] = mapped_column(String(34), nullable=True)
    counterparty_account_masked: Mapped[str | None] = mapped_column(String(34), nullable=True)

    narration: Mapped[str | None] = mapped_column(Text, nullable=True)
    narration_normalised: Mapped[str | None] = mapped_column(Text, nullable=True)

    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    original_amount_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    txn_timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    txn_timestamp_original: Mapped[str] = mapped_column(Text, nullable=False)
    source_timezone: Mapped[str] = mapped_column(String(50), nullable=False)
    original_reference_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalised_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)

    match_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="UNMATCHED"
    )

    __table_args__ = (
        CheckConstraint("direction IN ('DR', 'CR')", name="direction_valid"),
        CheckConstraint(
            "match_status IN ('UNMATCHED', 'CLAIMED', 'MATCHED')", name="match_status_valid"
        ),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        # The literal A6.2 index — created so R92 is satisfied exactly as
        # named, even though the blocking index below (WP4) is what
        # matching actually uses day to day. See docs/DATABASE.md (not
        # yet written) for the full justification of both.
        Index(
            "ix_normalised_transactions_txn_id_amount_currency_date",
            "txn_id",
            "amount",
            "currency",
            "txn_date",
        ),
        Index(
            "ix_normalised_transactions_unmatched_pool",
            "bank_code",
            "currency",
            "direction",
            "txn_date",
            "amount_minor",
            postgresql_where=text("match_status = 'UNMATCHED'"),
        ),
        {"postgresql_partition_by": "RANGE (txn_date)"},
    )
