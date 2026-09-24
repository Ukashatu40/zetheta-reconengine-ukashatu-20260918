# tests/integration/conftest.py
"""Fixtures for integration tests that require a real PostgreSQL instance.

Runs against a `recon_test` database inside the same PostgreSQL container
the dev stack uses. Requires, once:

    make migrate-test

Then TEST_DATABASE_URL must be set for these tests to actually run — the
Makefile's test-integration target sets it for you. Without it, every test
in this package is skipped, not failed — that's expected.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark every test collected under tests/integration/ as
    'integration'.

    A bare module-level `pytestmark = pytest.mark.integration` in a
    conftest.py does NOT propagate to sibling test modules in the same
    directory — it only marks tests defined in the conftest module itself,
    of which there are none here. This hook is the correct mechanism for
    directory-scoped auto-marking; it is what actually makes `-m
    integration` select these tests.
    """
    package_dir = os.path.join("tests", "integration")
    for item in items:
        item_path = str(item.fspath).replace(os.sep, "/")
        if package_dir.replace(os.sep, "/") in item_path:
            item.add_marker(pytest.mark.integration)


def _test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set; integration tests require a real "
            "PostgreSQL instance. See tests/integration/conftest.py for setup."
        )
    return url


@pytest.fixture(scope="session")
def engine() -> Generator[Engine, None, None]:
    eng = create_engine(_test_database_url(), pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def db_session(engine: Engine) -> Generator[Session, None, None]:
    """Roll back every write at the end of the test via a SAVEPOINT, so a
    test that deliberately provokes a database error (proving the audit
    trigger fires) doesn't poison the outer transaction the fixture needs
    for cleanup."""
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    outer_transaction.rollback()
    connection.close()
