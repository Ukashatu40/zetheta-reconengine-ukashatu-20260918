"""reconciliation_runs: mode, date range, PENDING status

Revision ID: a1b2c3d4e501
Revises: 46a40d24a359
Create Date: 2026-10-22 09:00:00.000000

Adds the run parameters the spec's POST /reconciliation/run needs (a date
range, A5/Day 5) and the FAST mode of the B4.3 backlog scenario. PENDING is
added so a background full run can be queued before it starts. Constraint
names follow IB-02: bare names are passed, the naming convention prefixes them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e501"
down_revision: str | None = "46a40d24a359"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DROP = "ALTER TABLE reconciliation_runs DROP CONSTRAINT ck_reconciliation_runs_{}"


def upgrade() -> None:
    op.add_column(
        "reconciliation_runs",
        sa.Column("mode", sa.String(10), nullable=False, server_default="FULL"),
    )
    op.add_column("reconciliation_runs", sa.Column("date_from", sa.Date(), nullable=True))
    op.add_column("reconciliation_runs", sa.Column("date_to", sa.Date(), nullable=True))
    op.execute(_DROP.format("status_valid"))
    op.create_check_constraint(
        "status_valid",
        "reconciliation_runs",
        "status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')",
    )
    op.create_check_constraint("mode_valid", "reconciliation_runs", "mode IN ('FAST', 'FULL')")
    op.create_check_constraint(
        "date_range_ordered",
        "reconciliation_runs",
        "date_from IS NULL OR date_to IS NULL OR date_from <= date_to",
    )


def downgrade() -> None:
    op.execute(_DROP.format("date_range_ordered"))
    op.execute(_DROP.format("mode_valid"))
    op.execute(_DROP.format("status_valid"))
    op.create_check_constraint(
        "status_valid", "reconciliation_runs", "status IN ('RUNNING', 'COMPLETED', 'FAILED')"
    )
    op.drop_column("reconciliation_runs", "date_to")
    op.drop_column("reconciliation_runs", "date_from")
    op.drop_column("reconciliation_runs", "mode")
