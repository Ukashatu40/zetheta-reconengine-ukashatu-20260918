"""match_claims exclusivity ledger

Revision ID: 526383ad4edd
Revises: 3f392c4a49dd
Create Date: 2026-09-27 11:42:23.646495

P4 Increment 1. Creates match_claims and its unique partial index —
the mechanism that makes double-matching a normalised transaction a
database-enforced impossibility. See
recon.persistence.models.matching.MatchClaim's module docstring for the
full reasoning.

Constraint names are bare (per IB-02,
docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md) — checked against \\d output
after applying, not assumed correct from this file alone.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "526383ad4edd"
down_revision: str | None = "3f392c4a49dd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "match_claims",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("normalised_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_result_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('INTERNAL', 'EXTERNAL', 'MEMBER')", name="role_valid"),
        sa.CheckConstraint("status IN ('ACTIVE', 'RELEASED')", name="status_valid"),
        sa.UniqueConstraint(
            "normalised_transaction_id", "match_result_id", name="uq_txn_id_match_result_id"
        ),
    )

    op.create_index(
        "ux_match_claims_one_active_claim_per_txn",
        "match_claims",
        ["normalised_transaction_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_table("match_claims")
