"""add weights_version to match_results

Revision ID: d076dea60820
Revises: 5fbf8a10121b
Create Date: 2026-09-29 08:47:34.444748

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d076dea60820"
down_revision: str | None = "5fbf8a10121b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("match_results", sa.Column("weights_version", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("match_results", "weights_version")
