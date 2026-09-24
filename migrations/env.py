# migrations/env.py
"""Alembic environment.

The database URL is read from DATABASE_URL at runtime, never hardcoded here
or in alembic.ini, so no credential is ever committed.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from recon.persistence.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL must be set to run migrations")

config.set_main_option("sqlalchemy.url", database_url)

# Importing recon.persistence.models registers every ORM table on
# Base.metadata. This enables `alembic check` and autogenerate for any
# future non-partitioned table. Partitioned tables (raw_transactions) and
# anything touching roles, grants or triggers are still hand-written — see
# the core-persistence migration's docstring for why autogenerate doesn't
# reliably diff PostgreSQL declarative partitioning.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
