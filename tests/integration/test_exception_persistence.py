# tests/integration/test_exception_persistence.py
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recon.domain.enums import ExceptionCategory
from recon.persistence.models import ReconException


def _row(**overrides: object) -> ReconException:
    defaults: dict[str, object] = {
        "dedupe_key": uuid.uuid4().hex,
        "category": "MISSING_INTERNAL",
        "severity": "HIGH",
        "status": "OPEN",
        "assigned_tier": 2,
        "bank_code": "HDFC",
        "affected_records": {},
        "suggested_resolution": "x",
        "rationale": "y",
        "sla_deadline": datetime(2026, 3, 15, 16, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return ReconException(**defaults)


@pytest.mark.parametrize("category", [c.value for c in ExceptionCategory])
def test_every_enum_category_is_accepted_by_the_database(
    db_session: Session, category: str
) -> None:
    """Guards drift between the enum and the literal list in the migration."""
    db_session.add(_row(category=category))
    db_session.flush()


def test_unknown_category_is_rejected(db_session: Session) -> None:
    db_session.add(_row(category="NOT_A_CATEGORY"))
    with pytest.raises(IntegrityError, match=r"category_valid|check"):
        db_session.flush()


@pytest.mark.parametrize("tier", [0, 5])
def test_tier_outside_one_to_four_is_rejected(db_session: Session, tier: int) -> None:
    db_session.add(_row(assigned_tier=tier))
    with pytest.raises(IntegrityError, match=r"tier_valid|check"):
        db_session.flush()


def test_duplicate_dedupe_key_is_rejected(db_session: Session) -> None:
    db_session.add(_row(dedupe_key="same"))
    db_session.flush()
    db_session.add(_row(dedupe_key="same"))
    with pytest.raises(IntegrityError, match=r"dedupe_key|unique"):
        db_session.flush()
