# src/recon/persistence/models/matching.py
"""ORM model for match_claims — the exclusivity ledger.

A normalised_transaction has at most one ACTIVE claim at any time. This
is enforced by the unique partial index below, not by application logic
alone: two concurrent attempts to claim the same row will have one
succeed and one raise IntegrityError, deterministically, regardless of
timing. This is what makes "an internal transaction consumed twice"
(A3.1's stated concern) a database-level impossibility rather than a
code-review hope.

match_result_id is nullable and has no foreign key yet, since
match_results does not exist until a later increment (exact matching).
A claim can exist before the match_result row that "owns" it is
finalised — see recon.matching.claims for the two-phase reasoning.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.persistence.models.base import Base


class MatchClaim(Base):
    __tablename__ = "match_claims"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    normalised_transaction_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    match_result_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="ACTIVE")
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("role IN ('INTERNAL', 'EXTERNAL', 'MEMBER')", name="role_valid"),
        CheckConstraint("status IN ('ACTIVE', 'RELEASED')", name="status_valid"),
        UniqueConstraint(
            "normalised_transaction_id", "match_result_id", name="uq_txn_id_match_result_id"
        ),
        Index(
            "ux_match_claims_one_active_claim_per_txn",
            "normalised_transaction_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )
