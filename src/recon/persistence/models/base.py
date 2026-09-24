# src/recon/persistence/models/base.py
"""Declarative base and naming convention for all ORM models.

The naming convention is what Alembic's autogenerate uses to name
constraints and indexes consistently. Without it, autogenerate produces
differently-named constraints on every run, turning migration diffs into
noisy DROP/CREATE pairs for constraints that never actually changed.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
