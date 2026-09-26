"""normalised transactions table

Revision ID: 7a48a435f36a
Revises: 832c157e58c3
Create Date: 2026-09-26 18:57:49.945741

WP3 closing increment. Creates normalised_transactions, partitioned by
RANGE (txn_date) — matching's own filter column, unlike raw_transactions
which partitions by ingested_on for retention purposes. Hand-written for
the same reason the WP1 core-persistence migration was: Alembic's
autogenerate does not reliably diff PostgreSQL declarative partitioning.

Partition strategy: monthly partitions for 2025-01 through 2026-12 plus a
DEFAULT partition, mirroring raw_transactions' migration exactly. Same
honest scope note applies: automated partition rotation beyond December
2026 is not implemented here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7a48a435f36a"
down_revision: str | None = "832c157e58c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DECEMBER = 12


def _monthly_partition_bounds(start: date, end: date) -> list[tuple[date, date]]:
    bounds: list[tuple[date, date]] = []
    current = start
    while current < end:
        nxt = (
            date(current.year + 1, 1, 1)
            if current.month == _DECEMBER
            else date(current.year, current.month + 1, 1)
        )
        bounds.append((current, nxt))
        current = nxt
    return bounds


def upgrade() -> None:
    op.create_table(
        "normalised_transactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("txn_date", sa.Date(), nullable=False),
        sa.Column("raw_transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "ingestion_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "ingestion_files.id", name="fk_normalised_transactions_ingestion_file_id"
            ),
            nullable=False,
        ),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("bank_code", sa.String(20), nullable=False),
        sa.Column("format_type", sa.String(20), nullable=False),
        sa.Column("source_line_no", sa.Integer(), nullable=True),
        sa.Column("config_version", sa.String(50), nullable=False),
        sa.Column("txn_id", sa.String(200), nullable=False),
        sa.Column("bank_ref", sa.String(200), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("currency_exponent", sa.SmallInteger(), nullable=False),
        sa.Column("currency_source", sa.String(30), nullable=False),
        sa.Column("original_currency", sa.String(3), nullable=True),
        sa.Column("direction", sa.String(2), nullable=False),
        sa.Column("is_reversal", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reverses_reference", sa.String(200), nullable=True),
        sa.Column("counterparty_name", sa.Text(), nullable=True),
        sa.Column("counterparty_name_normalised", sa.Text(), nullable=True),
        sa.Column("counterparty_account", sa.String(34), nullable=True),
        sa.Column("counterparty_account_masked", sa.String(34), nullable=True),
        sa.Column("narration", sa.Text(), nullable=True),
        sa.Column("narration_normalised", sa.Text(), nullable=True),
        sa.Column("settlement_date", sa.Date(), nullable=True),
        sa.Column("original_amount_text", sa.Text(), nullable=True),
        sa.Column("txn_timestamp_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("txn_timestamp_original", sa.Text(), nullable=False),
        sa.Column("source_timezone", sa.String(50), nullable=False),
        sa.Column("original_reference_text", sa.Text(), nullable=True),
        sa.Column("normalised_reference", sa.String(200), nullable=True),
        sa.Column("match_status", sa.String(20), nullable=False, server_default="UNMATCHED"),
        sa.PrimaryKeyConstraint("id", "txn_date", name="pk_normalised_transactions"),
        sa.CheckConstraint(
            "direction IN ('DR', 'CR')", name="ck_normalised_transactions_direction_valid"
        ),
        sa.CheckConstraint(
            "match_status IN ('UNMATCHED', 'CLAIMED', 'MATCHED')",
            name="ck_normalised_transactions_match_status_valid",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_normalised_transactions_amount_non_negative"),
        postgresql_partition_by="RANGE (txn_date)",
    )

    op.create_index(
        "ix_normalised_transactions_txn_id_amount_currency_date",
        "normalised_transactions",
        ["txn_id", "amount", "currency", "txn_date"],
    )
    op.create_index(
        "ix_normalised_transactions_unmatched_pool",
        "normalised_transactions",
        ["bank_code", "currency", "direction", "txn_date", "amount_minor"],
        postgresql_where=sa.text("match_status = 'UNMATCHED'"),
    )

    for lower, upper in _monthly_partition_bounds(date(2025, 1, 1), date(2027, 1, 1)):
        partition_name = f"normalised_transactions_{lower.strftime('%Y_%m')}"
        op.execute(f"""
            CREATE TABLE {partition_name}
            PARTITION OF normalised_transactions
            FOR VALUES FROM ('{lower.isoformat()}') TO ('{upper.isoformat()}')
            """)
    op.execute("""
        CREATE TABLE normalised_transactions_default
        PARTITION OF normalised_transactions DEFAULT
        """)


def downgrade() -> None:
    op.drop_table("normalised_transactions")  # drops all partitions with it
