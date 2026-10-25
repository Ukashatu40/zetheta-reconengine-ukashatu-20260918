# src/recon/persistence/models/alerts.py
"""ORM models for alerts and the rolling statistics the anomaly rules read.

alerts is the single place every automated warning lands: the three C2 anomaly
rules, ingestion-volume and absence checks, the 99.5% match-rate and 5%
exception-rate alerts, duplicate settlements, balance mismatches and format
changes. dedupe_key makes raising an alert idempotent.

counterparty_stats is the rolling-statistics table Day 5 asks for: a running
mean and variance (Welford) of amounts per bank, counterparty, direction and
currency, plus how many distinct days the counterparty was active.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.persistence.models.base import Base

ALERT_TYPES = (
    "AMOUNT_DEVIATION",
    "VELOCITY",
    "ABSENCE",
    "DATA_VOLUME",
    "SUSPICIOUS_MATCH_RATE",
    "HIGH_EXCEPTION_RATE",
    "EXCEPTION_BACKLOG",
    "BALANCE_MISMATCH",
    "DUPLICATE_SETTLEMENT",
    "FORMAT_CHANGE_DETECTED",
    "FILE_TAMPERED",
    "PAN_EXPOSURE",
    "BALANCE_TXN_DIVERGENCE",
)
ALERT_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
ALERT_STATUSES = ("OPEN", "ACKNOWLEDGED")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    alert_type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="OPEN")
    bank_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Who must look at it: the compliance team (A7.1 clause 9.1, STR candidates) or operations.
    route_to: Mapped[str] = mapped_column(String(20), nullable=False, server_default="operations")
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(300), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_alerts_dedupe_key"),
        CheckConstraint(_in_list("alert_type", ALERT_TYPES), name="type_valid"),
        CheckConstraint(_in_list("severity", ALERT_SEVERITIES), name="severity_valid"),
        CheckConstraint(_in_list("status", ALERT_STATUSES), name="status_valid"),
        CheckConstraint("route_to IN ('operations', 'compliance')", name="route_valid"),
        Index("ix_alerts_status_created_at", "status", "created_at"),
        Index("ix_alerts_type_bank_code", "alert_type", "bank_code"),
    )


class CounterpartyStat(Base):
    __tablename__ = "counterparty_stats"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    bank_code: Mapped[str] = mapped_column(String(20), nullable=False)
    # A payment appears once in the ledger and once in the bank's file; keeping the
    # sides apart stops every payment being counted twice.
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    counterparty: Mapped[str] = mapped_column(String(200), nullable=False)
    direction: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    txn_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mean_minor: Mapped[Decimal] = mapped_column(Numeric(30, 6), nullable=False)
    m2: Mapped[Decimal] = mapped_column(Numeric(40, 6), nullable=False)  # sum of squared deviations
    active_days: Mapped[int] = mapped_column(Integer, nullable=False)
    last_active_date: Mapped[date] = mapped_column(Date, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint(
            "bank_code",
            "source",
            "counterparty",
            "direction",
            "currency",
            name="uq_counterparty_stats_key",
        ),
        CheckConstraint("txn_count > 0", name="count_positive"),
        CheckConstraint("source IN ('INTERNAL', 'EXTERNAL')", name="source_valid"),
    )
