# src/recon/api/deps.py
"""FastAPI dependencies shared by all routes.

get_db does NOT commit. Write endpoints call session.commit() explicitly,
so a failed commit becomes an error response instead of depending on when
FastAPI runs cleanup code relative to sending the response.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from recon.persistence.session import get_engine, get_session_factory


def get_db() -> Iterator[Session]:
    session = get_session_factory(get_engine())()
    try:
        yield session
    finally:
        session.close()  # rolls back anything that was not committed


def get_now() -> datetime:
    return datetime.now(UTC)


def get_clock() -> Callable[[], datetime]:
    return lambda: datetime.now(UTC)


SessionDep = Annotated[Session, Depends(get_db)]
NowDep = Annotated[datetime, Depends(get_now)]
ClockDep = Annotated[Callable[[], datetime], Depends(get_clock)]
