# src/recon/persistence/models/config_store.py
"""ORM model for bank_configurations: a versioned history of every bank
configuration the service has loaded (Day 1, Day 2).

The YAML files stay the source of truth and the thing operators edit; this table
records WHAT was live and WHEN, keyed by content hash, so any reconciliation can
be tied to the exact configuration that produced it (A7.2 reproducibility).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.persistence.models.base import Base


class BankConfiguration(Base):
    __tablename__ = "bank_configurations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    bank_code: Mapped[str] = mapped_column(String(20), nullable=False)
    config_version: Mapped[str] = mapped_column(String(50), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    loaded_by: Mapped[str] = mapped_column(String(100), nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("bank_code", "content_sha256", name="uq_bank_configurations_version"),
        Index(
            "ux_bank_configurations_one_active_per_bank",
            "bank_code",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )
