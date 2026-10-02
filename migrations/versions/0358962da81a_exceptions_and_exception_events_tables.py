"""exceptions and exception_events tables

Revision ID: 0358962da81a
Revises: d076dea60820
Create Date: 2026-10-02 11:43:08.155182

R63. Check-constraint names are bare (IB-02) and are verified against
\\d output after applying. The category/severity/status lists are literal
here on purpose: a migration must not import application enums, or a
later enum change would silently rewrite history. The persistence tests
insert every ExceptionCategory value to catch drift.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0358962da81a"
down_revision: str | None = "d076dea60820"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CATEGORIES = (
    "MISSING_INTERNAL", "MISSING_EXTERNAL", "AMOUNT_MISMATCH", "DUPLICATE_INTERNAL",
    "DUPLICATE_EXTERNAL", "DATE_MISMATCH", "CURRENCY_MISMATCH", "DIRECTION_REVERSAL",
    "PARTIAL_MATCH", "NETTED_SETTLEMENT", "FEE_DEDUCTION", "FX_VARIANCE",
    "STALE_TRANSACTION", "FORMAT_ERROR", "REFERENCE_TRUNCATED", "TIMEZONE_OFFSET",
    "REVERSAL_PENDING", "REGULATORY_HOLD", "SETTLEMENT_DELAY",
)  # fmt: skip
_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
_STATUSES = ("OPEN", "IN_REVIEW", "ESCALATED", "RESOLVED", "WRITTEN_OFF", "REOPENED")
_EVENT_TYPES = ("CREATED", "ESCALATED", "SLA_BREACHED", "ASSIGNED", "RESOLVED", "REOPENED")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "exceptions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("assigned_tier", sa.SmallInteger(), nullable=False),
        sa.Column("assigned_to", sa.String(100), nullable=True),
        sa.Column("bank_code", sa.String(20), nullable=False),
        sa.Column("run_id", sa.String(100), nullable=True),
        sa.Column("ingestion_file_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("affected_records", postgresql.JSONB(), nullable=False),
        sa.Column("financial_impact_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("financial_impact_currency", sa.String(3), nullable=True),
        sa.Column("financial_impact_inr_minor", sa.BigInteger(), nullable=True),
        sa.Column("suggested_resolution", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("sla_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sla_breached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(100), nullable=True),
        sa.Column("resolution_details", sa.Text(), nullable=True),
        sa.UniqueConstraint("dedupe_key", name="uq_exceptions_dedupe_key"),
        sa.CheckConstraint(_in("category", _CATEGORIES), name="category_valid"),
        sa.CheckConstraint(_in("severity", _SEVERITIES), name="severity_valid"),
        sa.CheckConstraint(_in("status", _STATUSES), name="status_valid"),
        sa.CheckConstraint("assigned_tier BETWEEN 1 AND 4", name="tier_valid"),
    )
    op.create_index(
        "ix_exceptions_open_sla",
        "exceptions",
        ["status", "sla_deadline"],
        postgresql_where=sa.text("status = 'OPEN'"),
    )
    op.create_index(
        "ix_exceptions_sla_scan",
        "exceptions",
        ["sla_deadline"],
        postgresql_where=sa.text(
            "sla_breached_at IS NULL AND status IN ('OPEN', 'IN_REVIEW', 'ESCALATED', 'REOPENED')"
        ),
    )

    op.create_table(
        "exception_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "exception_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exceptions.id", name="fk_exception_events_exception_id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("from_tier", sa.SmallInteger(), nullable=True),
        sa.Column("to_tier", sa.SmallInteger(), nullable=True),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in("event_type", _EVENT_TYPES), name="event_type_valid"),
    )
    op.create_index(
        "ix_exception_events_exception_id_occurred_at",
        "exception_events",
        ["exception_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("exception_events")
    op.drop_table("exceptions")
