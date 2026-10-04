"""reconciliation_runs table

Revision ID: 46a40d24a359
Revises: ff99fd2719ae
Create Date: 2026-10-04 10:43:02.781522

The partial unique index allows at most one RUNNING run per bank. Constraint
names follow IB-02 (bare check name); verify against \\d after applying.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "46a40d24a359"
down_revision: str | None = "ff99fd2719ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reconciliation_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("bank_code", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="RUNNING"),
        sa.Column("requested_by", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics", postgresql.JSONB(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_reconciliation_runs_idempotency_key"),
        sa.CheckConstraint("status IN ('RUNNING', 'COMPLETED', 'FAILED')", name="status_valid"),
    )
    op.create_index(
        "ux_reconciliation_runs_one_running_per_bank",
        "reconciliation_runs",
        ["bank_code"],
        unique=True,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.create_index("ix_reconciliation_runs_created_at", "reconciliation_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("reconciliation_runs")
