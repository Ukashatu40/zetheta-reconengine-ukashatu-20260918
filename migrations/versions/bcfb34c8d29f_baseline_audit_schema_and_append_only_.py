"""baseline: audit schema and append-only role

Revision ID: bcfb34c8d29f
Revises:
Create Date: 2026-09-21 12:00:33.374336

Establishes the append-only enforcement mechanism for the audit trail
(PDF A4.3, corrected per docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md AE-19).

The PDF's phrase "append-only, stored separately from the operational
database" cannot be taken literally alongside Day 1's requirement that
audit_log is one of the eight tables in the same schema. This migration
resolves the tension honestly: audit_log (created in a later migration)
lives in a dedicated `audit` schema within the same database, owned by a
role that only has INSERT and SELECT — UPDATE and DELETE are explicitly
revoked at the database level. A defence-in-depth trigger is added once
the table itself exists.

This is NOT physical WORM storage and does not claim to be. A PostgreSQL
superuser can still alter the table structure or use ALTER TABLE to bypass
privileges. The hash chain (added later) is what makes tampering
detectable even by a superuser; this migration is what makes it
inconvenient for anyone with only application-level credentials.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "bcfb34c8d29f"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- audit schema -----------------------------------------------------
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")

    # --- dedicated append-only role ----------------------------------------
    # Password is intentionally not set here. In every environment this role
    # authenticates via the AUDIT_DB_USER / AUDIT_DB_PASSWORD environment
    # variables through the application's connection pool configuration, not
    # via a password embedded in a migration. Locally, set it once:
    #   ALTER ROLE recon_audit WITH PASSWORD '<value of AUDIT_DB_PASSWORD>';
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'recon_audit') THEN
                CREATE ROLE recon_audit WITH LOGIN;
            END IF;
        END
        $$;
        """)

    op.execute("GRANT USAGE ON SCHEMA audit TO recon_audit")

    # No tables exist in the audit schema yet (that's WP1 Increment 4), so
    # there is nothing to GRANT INSERT/SELECT or REVOKE UPDATE/DELETE on
    # yet. Setting the DEFAULT PRIVILEGES now means every future table
    # created in this schema by the migration owner automatically inherits
    # the correct restricted grants, without a human remembering to repeat
    # this on every subsequent migration.
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA audit
        GRANT INSERT, SELECT ON TABLES TO recon_audit
        """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES IN SCHEMA audit
        REVOKE UPDATE, DELETE ON TABLES FROM recon_audit
        """)

    # The application's normal connection role (recon_app, i.e. whatever
    # POSTGRES_USER is) also gets no UPDATE/DELETE on the audit schema by
    # default privilege, so even a bug in application code using the wrong
    # connection cannot mutate an existing audit row.
    op.execute(f"""
        ALTER DEFAULT PRIVILEGES IN SCHEMA audit
        REVOKE UPDATE, DELETE ON TABLES FROM {op.get_bind().engine.url.username}
        """)


def downgrade() -> None:
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA audit REVOKE ALL ON TABLES FROM recon_audit")
    op.execute("DROP ROLE IF EXISTS recon_audit")
    op.execute("DROP SCHEMA IF EXISTS audit CASCADE")
