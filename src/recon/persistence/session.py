# src/recon/persistence/session.py
"""Database engine and session factory for application runtime use.

Distinct from migrations/env.py, which manages its own engine for schema
changes. This is what repositories (and, later, recon.audit.logger) use
during normal operation.

Two engines are exposed. get_engine() is the application's normal
connection role (POSTGRES_USER / DATABASE_URL). get_audit_engine() is the
dedicated recon_audit role (AUDIT_DB_USER / AUDIT_DB_PASSWORD).
Application code must never write to audit.audit_log through get_engine()
— only through get_audit_engine() — because recon_app owns that table and
REVOKE does not restrict an owner (see the core-persistence migration's
docstring). The BEFORE UPDATE/DELETE trigger is the actual backstop; this
separation is what keeps normal application code from attempting the
mutation in the first place.
"""

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Engine for the application's normal connection role."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set")
    return create_engine(
        database_url,
        pool_size=_int_env("DB_POOL_SIZE", 10),
        max_overflow=_int_env("DB_MAX_OVERFLOW", 20),
        pool_timeout=_int_env("DB_POOL_TIMEOUT_SECONDS", 30),
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def get_audit_engine() -> Engine:
    """Engine for the dedicated append-only audit role (recon_audit).

    Built by substituting AUDIT_DB_USER / AUDIT_DB_PASSWORD into the same
    host/port/database as DATABASE_URL, rather than requiring a second
    full URL to be configured and kept in sync by hand.
    """
    database_url = os.environ.get("DATABASE_URL")
    audit_user = os.environ.get("AUDIT_DB_USER")
    audit_password = os.environ.get("AUDIT_DB_PASSWORD")
    if not (database_url and audit_user and audit_password):
        raise RuntimeError("DATABASE_URL, AUDIT_DB_USER and AUDIT_DB_PASSWORD must all be set")
    url = make_url(database_url).set(username=audit_user, password=audit_password)
    return create_engine(url, pool_size=5, pool_pre_ping=True)


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
