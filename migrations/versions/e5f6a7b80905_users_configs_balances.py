"""users, bank_configurations and statement_balances tables; api_keys.user_id; new alert types

Revision ID: e5f6a7b80905
Revises: d4e5f6a70804
Create Date: 2026-10-24 09:00:00.000000

users completes the Day 1 table list (A9.2: ADMIN manages users); an API key may
belong to a user. bank_configurations is the versioned record of loaded bank
configs. statement_balances holds the balance-level reconciliation of each
MT940/CAMT.053 statement (Case Study C5). The alerts type check gains PAN_EXPOSURE
and the balance-while-transactions-pass alert. Constraint names follow IB-02.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f6a7b80905"
down_revision: str | None = "d4e5f6a70804"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_TYPES = (
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
_NEW_TYPES = (*_OLD_TYPES, "PAN_EXPOSURE", "BALANCE_TXN_DIVERGENCE")
_DROP_TYPE_CHECK = "ALTER TABLE alerts DROP CONSTRAINT ck_alerts_type_valid"
_DROP_INTEGRITY_CHECK = (
    "ALTER TABLE ingestion_files DROP CONSTRAINT ck_ingestion_files_integrity_status_valid"
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.add_column("ingestion_files", sa.Column("integrity_status", sa.String(20), nullable=True))
    op.add_column(
        "ingestion_files",
        sa.Column("integrity_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "integrity_status_valid",
        "ingestion_files",
        "integrity_status IS NULL OR integrity_status IN ('OK', 'TAMPERED', 'MISSING')",
    )
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("username", name="uq_users_username"),
        sa.CheckConstraint("role IN ('VIEWER', 'ANALYST', 'ADMIN', 'SYSTEM')", name="role_valid"),
    )
    op.add_column("api_keys", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_api_keys_user_id_users", "api_keys", "users", ["user_id"], ["id"])

    op.create_table(
        "bank_configurations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("bank_code", sa.String(20), nullable=False),
        sa.Column("config_version", sa.String(50), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("config_json", postgresql.JSONB(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("loaded_by", sa.String(100), nullable=False),
        sa.Column(
            "loaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("bank_code", "content_sha256", name="uq_bank_configurations_version"),
    )
    op.create_index(
        "ux_bank_configurations_one_active_per_bank",
        "bank_configurations",
        ["bank_code"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "statement_balances",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "ingestion_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_files.id", name="fk_statement_balances_ingestion_file_id"),
            nullable=False,
        ),
        sa.Column("bank_code", sa.String(20), nullable=False),
        sa.Column("statement_ref", sa.String(100), nullable=True),
        sa.Column("account", sa.String(64), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("opening_minor", sa.BigInteger(), nullable=False),
        sa.Column("closing_minor", sa.BigInteger(), nullable=False),
        sa.Column("computed_closing_minor", sa.BigInteger(), nullable=False),
        sa.Column("net_entries_minor", sa.BigInteger(), nullable=False),
        sa.Column("difference_minor", sa.BigInteger(), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("unparseable_entries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("balanced", sa.Boolean(), nullable=False),
        sa.Column("opening_date", sa.Date(), nullable=True),
        sa.Column("closing_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("ingestion_file_id", name="uq_statement_balances_ingestion_file_id"),
        sa.CheckConstraint("entry_count >= 0", name="entry_count_non_negative"),
    )

    op.execute(_DROP_TYPE_CHECK)
    op.create_check_constraint("type_valid", "alerts", _in("alert_type", _NEW_TYPES))


def downgrade() -> None:
    op.execute(_DROP_TYPE_CHECK)
    op.create_check_constraint("type_valid", "alerts", _in("alert_type", _OLD_TYPES))
    op.drop_table("statement_balances")
    op.execute(_DROP_INTEGRITY_CHECK)
    op.drop_column("ingestion_files", "integrity_checked_at")
    op.drop_column("ingestion_files", "integrity_status")
    op.drop_index("ux_bank_configurations_one_active_per_bank", table_name="bank_configurations")
    op.drop_table("bank_configurations")
    op.drop_constraint("fk_api_keys_user_id_users", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "user_id")
    op.drop_table("users")
