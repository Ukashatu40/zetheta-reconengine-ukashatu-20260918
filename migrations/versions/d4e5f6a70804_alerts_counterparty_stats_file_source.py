"""alerts and counterparty_stats tables; ingestion_files.source and stored_path

Revision ID: d4e5f6a70804
Revises: c3d4e5f60703
Create Date: 2026-10-23 09:00:00.000000

alerts holds every automated warning (anomaly rules, rate alerts, duplicate
settlements, balance mismatches, format changes). counterparty_stats is the
rolling-statistics table of Day 5. ingestion_files gains source (INTERNAL or
EXTERNAL) and stored_path (so file integrity can be re-verified, Case Study C4).
Constraint names follow IB-02.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a70804"
down_revision: str | None = "c3d4e5f60703"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TYPES = (
    "AMOUNT_DEVIATION",
    "VELOCITY",
    "ABSENCE",
    "DATA_VOLUME",
    "SUSPICIOUS_MATCH_RATE",
    "HIGH_EXCEPTION_RATE",
    "EXCEPTION_BACKLOG",
    "BALANCE_MISMATCH",
    "DUPLICATE_SETTLEMENT",
    "FORMAT_CHANGE_DETECTED",
    "FILE_TAMPERED",
)


_SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.add_column("ingestion_files", sa.Column("source", sa.String(20), nullable=True))
    op.add_column("ingestion_files", sa.Column("stored_path", sa.Text(), nullable=True))
    scanned_at = sa.Column("anomaly_scanned_at", sa.DateTime(timezone=True), nullable=True)
    op.add_column("ingestion_files", scanned_at)

    op.create_table(
        "alerts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("alert_type", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("bank_code", sa.String(20), nullable=True),
        sa.Column("route_to", sa.String(20), nullable=False, server_default="operations"),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("dedupe_key", sa.String(300), nullable=False),
        sa.Column("run_id", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(100), nullable=True),
        sa.UniqueConstraint("dedupe_key", name="uq_alerts_dedupe_key"),
        sa.CheckConstraint(_in("alert_type", _TYPES), name="type_valid"),
        sa.CheckConstraint(_in("severity", _SEVERITIES), name="severity_valid"),
        sa.CheckConstraint(_in("status", ("OPEN", "ACKNOWLEDGED")), name="status_valid"),
        sa.CheckConstraint("route_to IN ('operations', 'compliance')", name="route_valid"),
    )
    op.create_index("ix_alerts_status_created_at", "alerts", ["status", "created_at"])
    op.create_index("ix_alerts_type_bank_code", "alerts", ["alert_type", "bank_code"])

    op.create_table(
        "counterparty_stats",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("bank_code", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("counterparty", sa.String(200), nullable=False),
        sa.Column("direction", sa.String(2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("txn_count", sa.BigInteger(), nullable=False),
        sa.Column("mean_minor", sa.Numeric(30, 6), nullable=False),
        sa.Column("m2", sa.Numeric(40, 6), nullable=False),
        sa.Column("active_days", sa.Integer(), nullable=False),
        sa.Column("last_active_date", sa.Date(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "bank_code",
            "source",
            "counterparty",
            "direction",
            "currency",
            name="uq_counterparty_stats_key",
        ),
        sa.CheckConstraint("source IN ('INTERNAL', 'EXTERNAL')", name="source_valid"),
        sa.CheckConstraint("txn_count > 0", name="count_positive"),
    )


def downgrade() -> None:
    op.drop_table("counterparty_stats")
    op.drop_table("alerts")
    op.drop_column("ingestion_files", "anomaly_scanned_at")
    op.drop_column("ingestion_files", "stored_path")
    op.drop_column("ingestion_files", "source")
