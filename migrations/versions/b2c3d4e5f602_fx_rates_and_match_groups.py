"""fx_rates table; match_results.match_group_id

Revision ID: b2c3d4e5f602
Revises: a1b2c3d4e501
Create Date: 2026-10-22 11:00:00.000000

fx_rates holds historical INR-per-unit rates (A5.1). match_group_id ties the
member rows of a split or netted match together (A3.3): one row per
(internal, external) pairing in the group, all sharing the id. Constraint names
follow IB-02.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f602"
down_revision: str | None = "a1b2c3d4e501"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate_inr", sa.Numeric(18, 8), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("suspect", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("suspect_reason", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("rate_inr > 0", name="rate_positive"),
        sa.CheckConstraint("source IN ('RBI', 'FBIL', 'BANK_CARD')", name="source_valid"),
    )
    op.create_index(
        "uq_fx_rates_date_currency_source",
        "fx_rates",
        ["rate_date", "currency", "source"],
        unique=True,
    )
    op.create_index("ix_fx_rates_currency_date", "fx_rates", ["currency", "rate_date"])

    op.add_column(
        "match_results", sa.Column("match_group_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_index(
        "ix_match_results_match_group_id",
        "match_results",
        ["match_group_id"],
        postgresql_where=sa.text("match_group_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_match_results_match_group_id", table_name="match_results")
    op.drop_column("match_results", "match_group_id")
    op.drop_table("fx_rates")
