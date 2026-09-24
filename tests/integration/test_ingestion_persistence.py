# tests/integration/test_ingestion_persistence.py
"""Integration tests for IngestionFile and RawTransaction persistence.

Proves: unique content_sha256 rejects duplicate files (R33's database
backstop), the FK from raw_transactions to ingestion_files is enforced,
inserts route to the correct monthly partition, and a row dated outside
the pre-created range falls back to the DEFAULT partition rather than
being rejected.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recon.persistence.models import IngestionFile, RawTransaction


def _make_file(**overrides: object) -> IngestionFile:
    defaults: dict[str, object] = {
        "bank_code": "HDFC",
        "format_type": "CSV",
        "original_filename": "hdfc_settlement_20260315.csv",
        "content_sha256": "a" * 64,
        "size_bytes": 1024,
        "ingested_by": "test-suite",
    }
    defaults.update(overrides)
    return IngestionFile(**defaults)


def test_ingestion_file_persists_and_reads_back(db_session: Session) -> None:
    file = _make_file()
    db_session.add(file)
    db_session.flush()

    fetched = db_session.get(IngestionFile, file.id)
    assert fetched is not None
    assert fetched.content_sha256 == "a" * 64
    assert fetched.status == "PENDING"


def test_duplicate_content_hash_is_rejected(db_session: Session) -> None:
    """R33: the unique constraint is the database-level backstop for
    duplicate-file rejection."""
    db_session.add(_make_file(content_sha256="b" * 64))
    db_session.flush()

    db_session.add(_make_file(content_sha256="b" * 64, original_filename="duplicate.csv"))
    with pytest.raises(IntegrityError, match=r"content_sha256|unique"):
        db_session.flush()


def test_raw_transaction_requires_existing_ingestion_file(db_session: Session) -> None:
    orphan = RawTransaction(
        ingested_on=date(2026, 3, 15),
        ingestion_file_id=uuid.uuid4(),  # does not exist
        bank_code="HDFC",
        format_type="CSV",
        raw_payload={"ref": "X"},
    )
    db_session.add(orphan)
    with pytest.raises(IntegrityError, match=r"foreign key|violates"):
        db_session.flush()


def test_raw_transaction_routes_to_correct_monthly_partition(db_session: Session) -> None:
    file = _make_file(content_sha256="c" * 64)
    db_session.add(file)
    db_session.flush()

    txn = RawTransaction(
        ingested_on=date(2026, 3, 15),
        ingestion_file_id=file.id,
        bank_code="HDFC",
        format_type="CSV",
        raw_payload={"ref": "HDFC-1"},
    )
    db_session.add(txn)
    db_session.flush()

    partition = db_session.execute(
        text(
            "SELECT tableoid::regclass::text FROM raw_transactions "
            "WHERE id = :id AND ingested_on = :ingested_on"
        ),
        {"id": txn.id, "ingested_on": date(2026, 3, 15)},
    ).scalar_one()
    assert partition == "raw_transactions_2026_03"


def test_raw_transaction_outside_planned_range_falls_back_to_default_partition(
    db_session: Session,
) -> None:
    file = _make_file(content_sha256="d" * 64)
    db_session.add(file)
    db_session.flush()

    txn = RawTransaction(
        ingested_on=date(2030, 1, 1),  # outside the 2025-2026 range created by the migration
        ingestion_file_id=file.id,
        bank_code="HDFC",
        format_type="CSV",
        raw_payload={"ref": "HDFC-FUTURE"},
    )
    db_session.add(txn)
    db_session.flush()

    partition = db_session.execute(
        text(
            "SELECT tableoid::regclass::text FROM raw_transactions "
            "WHERE id = :id AND ingested_on = :ingested_on"
        ),
        {"id": txn.id, "ingested_on": date(2030, 1, 1)},
    ).scalar_one()
    assert partition == "raw_transactions_default"


def test_invalid_parse_status_is_rejected(db_session: Session) -> None:
    file = _make_file(content_sha256="e" * 64)
    db_session.add(file)
    db_session.flush()

    bad = RawTransaction(
        ingested_on=date(2026, 3, 15),
        ingestion_file_id=file.id,
        bank_code="HDFC",
        format_type="CSV",
        raw_payload={"ref": "HDFC-2"},
        parse_status="NOT_A_REAL_STATUS",
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError, match=r"parse_status_valid|check"):
        db_session.flush()
