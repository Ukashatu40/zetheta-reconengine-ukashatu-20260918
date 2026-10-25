"""The chaos tests needs the same PostgreSQL fixtures as the integration tests."""

from __future__ import annotations

from tests.integration.conftest import db_session, engine

__all__ = ["db_session", "engine"]
