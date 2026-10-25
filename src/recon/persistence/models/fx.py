# src/recon/persistence/models/fx.py
"""ORM model for fx_rates (A5.1): historical rates, INR per one unit of a
foreign currency, keyed by date, currency and source.

source ranks follow A5.1's hierarchy: RBI reference rate first, then FBIL,
then a bank's card rate. suspect marks a rate flagged as an outlier at load
time (D10 plants three wrong rates in the sample file); a suspect rate is
used only when nothing else exists for the date.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.persistence.models.base import Base

FX_SOURCES = ("RBI", "FBIL", "BANK_CARD")


class FxRate(Base):
    __tablename__ = "fx_rates"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    rate_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate_inr: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    suspect: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    suspect_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("rate_inr > 0", name="rate_positive"),
        CheckConstraint(
            "source IN (" + ", ".join(f"'{s}'" for s in FX_SOURCES) + ")", name="source_valid"
        ),
        Index("uq_fx_rates_date_currency_source", "rate_date", "currency", "source", unique=True),
        Index("ix_fx_rates_currency_date", "currency", "rate_date"),
    )
