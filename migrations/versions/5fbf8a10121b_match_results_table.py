"""match_results table

Revision ID: 5fbf8a10121b
Revises: 526383ad4edd
Create Date: 2026-09-27 16:35:37.537478

WP4 Increment 2. Creates match_results, per PDF A3.4's explainable-match
shape. See recon.persistence.models.match_results.MatchResult's module
docstring for field-by-field reasoning.

Constraint names are bare, verified against \\d output after applying —
per IB-02 (docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5fbf8a10121b"
down_revision: str | None = "526383ad4edd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "match_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_id", sa.String(100), nullable=False),
        sa.Column("match_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("internal_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_scores", postgresql.JSONB(), nullable=False),
        sa.Column("matched_fields", postgresql.JSONB(), nullable=False),
        sa.Column("hard_constraints_passed", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("rule_id", sa.String(100), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("matched_on_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reviewed_by", sa.String(100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            """match_type IN
            ('EXACT', 'FUZZY', 'RULE', 'SPLIT', 'NETTED', 'CROSS_CURRENCY', 'MANUAL')""",
            name="match_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('AUTO_MATCHED', 'PENDING_REVIEW', 'CONFIRMED', 'REJECTED')",
            name="status_valid",
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        sa.CheckConstraint(
            "status != 'AUTO_MATCHED' OR confidence > 0.85",
            name="auto_matched_requires_high_confidence",
        ),
    )

    op.create_index("ix_match_results_run_id", "match_results", ["run_id"])
    op.create_index("ix_match_results_internal_txn", "match_results", ["internal_transaction_id"])
    op.create_index("ix_match_results_external_txn", "match_results", ["external_transaction_id"])


def downgrade() -> None:
    op.drop_table("match_results")
