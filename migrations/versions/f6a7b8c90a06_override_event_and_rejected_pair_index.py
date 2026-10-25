"""exception_events OVERRIDDEN type; partial index on rejected match pairs

Revision ID: f6a7b8c90a06
Revises: e5f6a7b80905
Create Date: 2026-10-25 09:00:00.000000

An ADMIN overriding an exception's tier (A9.2) is recorded as an OVERRIDDEN event.
The partial index makes "was this pair unmatched by a person?" cheap for the exact
matchers, which must never re-match such a pair. Constraint names follow IB-02.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c90a06"
down_revision: str | None = "e5f6a7b80905"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = ("CREATED", "ESCALATED", "SLA_BREACHED", "ASSIGNED", "RESOLVED", "REOPENED")
_NEW = (*_OLD, "OVERRIDDEN")
_DROP_CHECK = "ALTER TABLE exception_events DROP CONSTRAINT ck_exception_events_event_type_valid"


def _in(values: Sequence[str]) -> str:
    return "event_type IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.execute(_DROP_CHECK)
    op.create_check_constraint("event_type_valid", "exception_events", _in(_NEW))
    op.create_index(
        "ix_match_results_rejected_pair",
        "match_results",
        ["internal_transaction_id", "external_transaction_id"],
        postgresql_where=sa.text("status = 'REJECTED'"),
    )


def downgrade() -> None:
    op.drop_index("ix_match_results_rejected_pair", table_name="match_results")
    op.execute(_DROP_CHECK)
    op.create_check_constraint("event_type_valid", "exception_events", _in(_OLD))
