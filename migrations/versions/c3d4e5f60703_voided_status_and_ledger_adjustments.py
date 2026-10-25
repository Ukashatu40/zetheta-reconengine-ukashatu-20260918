"""normalised_transactions VOIDED status; ledger_adjustments table

Revision ID: c3d4e5f60703
Revises: b2c3d4e5f602
Create Date: 2026-10-22 14:00:00.000000

An auto-resolved DUPLICATE_INTERNAL voids the duplicate (Day 4): the row stays,
marked VOIDED and linked to the original it duplicates, so nothing is deleted.
An auto-resolved FEE_DEDUCTION writes a ledger_adjustments row. Constraint names
follow IB-02.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f60703"
down_revision: str | None = "b2c3d4e5f602"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DROP_STATUS_CHECK = (
    "ALTER TABLE normalised_transactions "
    "DROP CONSTRAINT ck_normalised_transactions_match_status_valid"
)


def upgrade() -> None:
    op.add_column(
        "normalised_transactions",
        sa.Column("voided_original_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(_DROP_STATUS_CHECK)
    op.create_check_constraint(
        "match_status_valid",
        "normalised_transactions",
        "match_status IN ('UNMATCHED', 'CLAIMED', 'MATCHED', 'VOIDED')",
    )

    op.create_table(
        "ledger_adjustments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "exception_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exceptions.id", name="fk_ledger_adjustments_exception_id"),
            nullable=False,
        ),
        sa.Column("bank_code", sa.String(20), nullable=False),
        sa.Column("adjustment_type", sa.String(20), nullable=False),
        sa.Column("internal_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("exception_id", name="uq_ledger_adjustments_exception_id"),
        sa.CheckConstraint("amount_minor > 0", name="amount_positive"),
        sa.CheckConstraint("adjustment_type IN ('BANK_FEE')", name="type_valid"),
    )


def downgrade() -> None:
    op.drop_table("ledger_adjustments")
    op.execute(_DROP_STATUS_CHECK)
    op.create_check_constraint(
        "match_status_valid",
        "normalised_transactions",
        "match_status IN ('UNMATCHED', 'CLAIMED', 'MATCHED')",
    )
    op.drop_column("normalised_transactions", "voided_original_id")
