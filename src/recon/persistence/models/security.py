# src/recon/persistence/models/security.py
"""ORM model for api_keys. Only the SHA-256 hash of a key is stored."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.persistence.models.base import Base

ROLE_VALUES = ("VIEWER", "ANALYST", "ADMIN", "SYSTEM")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        UniqueConstraint("name", name="uq_api_keys_name"),
        CheckConstraint(
            "role IN (" + ", ".join(f"'{r}'" for r in ROLE_VALUES) + ")", name="role_valid"
        ),
    )
