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

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL must be set to run migrations")

# Alembic's engine_from_config expects a psycopg2-style URL section; we
# override the ini value directly with the env-sourced URL instead.
config.set_main_option("sqlalchemy.url", database_url)

# target_metadata is None for this baseline migration — it creates the audit
# schema and its privileges by raw SQL, not via ORM metadata. WP1 Increment 4
# introduces recon.persistence.models and wires target_metadata to it for
# autogenerate support on subsequent migrations.
target_metadata = None


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
