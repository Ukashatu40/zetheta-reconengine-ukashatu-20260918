"""api_keys table

Revision ID: ff99fd2719ae
Revises: 0358962da81a
Create Date: 2026-10-03 09:08:33.972903

Only the SHA-256 hash of a key is stored. Constraint names follow IB-02:
the check constraint is bare, unique constraints carry their full name.
Verify against \\d api_keys after applying.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ff99fd2719ae"
down_revision: str | None = "0358962da81a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        sa.UniqueConstraint("name", name="uq_api_keys_name"),
        sa.CheckConstraint("role IN ('VIEWER', 'ANALYST', 'ADMIN', 'SYSTEM')", name="role_valid"),
    )


def downgrade() -> None:
    op.drop_table("api_keys")
