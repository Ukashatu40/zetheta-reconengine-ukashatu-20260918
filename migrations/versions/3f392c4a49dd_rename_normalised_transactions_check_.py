"""rename normalised_transactions check constraints to correct names

Revision ID: 3f392c4a49dd
Revises: 7a48a435f36a
Create Date: 2026-09-26 22:08:32.137007

Fixes a constraint-naming bug in 7a48a435f36a: that migration passed
already-prefixed names (e.g. "ck_normalised_transactions_direction_valid")
directly to sa.CheckConstraint, but Alembic's raw DDL path does not apply
the ORM's naming convention (base.py's NAMING_CONVENTION template already
adds the "ck_<table>_" prefix). The result was double-prefixed names that
Postgres silently truncated and hash-suffixed
(ck_normalised_transactions_ck_normalised_transactions_d_2577, etc). This
is the third occurrence of this exact bug pattern — see
docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md for the first two, in
ingestion_files and raw_transactions (WP1 Increment 4), which were fixed
before ever being applied. This one had already run against dev and test
databases before being caught, hence a rename migration rather than an
edit to the original file.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "3f392c4a49dd"
down_revision: str | None = "7a48a435f36a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The exact broken names differ per-database (Postgres's truncate+hash is
# deterministic per constraint DEFINITION, but only if column order and
# text are byte-identical across both databases, which they are here
# since both ran the same migration file). Confirmed the same names
# appear on both dev and test in this case.
_RENAMES = [
    (
        "ck_normalised_transactions_ck_normalised_transactions_d_2577",
        "ck_normalised_transactions_direction_valid",
    ),
    (
        "ck_normalised_transactions_ck_normalised_transactions_m_fd38",
        "ck_normalised_transactions_match_status_valid",
    ),
    (
        "ck_normalised_transactions_ck_normalised_transactions_a_1af0",
        "ck_normalised_transactions_amount_non_negative",
    ),
]


def upgrade() -> None:
    for old_name, new_name in _RENAMES:
        op.execute(
            f'ALTER TABLE normalised_transactions RENAME CONSTRAINT "{old_name}" TO "{new_name}"'
        )


def downgrade() -> None:
    for old_name, new_name in _RENAMES:
        op.execute(
            f'ALTER TABLE normalised_transactions RENAME CONSTRAINT "{new_name}" TO "{old_name}"'
        )
