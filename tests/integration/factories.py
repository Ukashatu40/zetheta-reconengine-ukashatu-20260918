# tests/integration/factories.py
"""Shared builders for integration tests that need real, FK-valid rows."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from recon.matching.claims import ClaimsService
from recon.persistence.models import IngestionFile, MatchResult, NormalisedTransaction


def make_ingestion_file(session: Session, **overrides: object) -> IngestionFile:
    defaults: dict[str, object] = {
        "bank_code": "HDFC",
        "format_type": "CSV",
        "original_filename": "test.csv",
        "content_sha256": uuid.uuid4().hex + uuid.uuid4().hex,
        "size_bytes": 100,
        "ingested_by": "test-suite",
    }
    defaults.update(overrides)
    ingestion_file = IngestionFile(**defaults)
    session.add(ingestion_file)
    session.flush()
    return ingestion_file


def make_txn(ingestion_file_id: uuid.UUID, **overrides: object) -> NormalisedTransaction:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "txn_date": date(2026, 3, 15),
        "raw_transaction_id": uuid.uuid4(),
        "ingestion_file_id": ingestion_file_id,
        "source": "INTERNAL",
        "bank_code": "HDFC",
        "format_type": "CSV",
        "config_version": "hdfc.v1",
        "txn_id": "REF001",
        "amount": Decimal("1500.00"),
        "amount_minor": 150_000,
        "currency": "INR",
        "currency_exponent": 2,
        "currency_source": "ENTRY_LEVEL",
        "direction": "CR",
        "is_reversal": False,
        "txn_timestamp_utc": datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
        "txn_timestamp_original": "15-03-2026",
        "source_timezone": "Asia/Kolkata",
        "normalised_reference": "REF001",
        "counterparty_name_normalised": None,
        "match_status": "UNMATCHED",
    }
    defaults.update(overrides)
    return NormalisedTransaction(**defaults)


def make_pending_review_pair(
    session: Session,
) -> tuple[NormalisedTransaction, NormalisedTransaction, MatchResult]:
    """Two unmatched transactions held by a PENDING_REVIEW result with both claims ACTIVE,
    exactly as the fuzzy strategy leaves a review-band pair."""
    ingestion_file = make_ingestion_file(session)
    internal = make_txn(ingestion_file.id, source="INTERNAL")
    external = make_txn(
        ingestion_file.id, source="EXTERNAL", id=uuid.uuid4(), raw_transaction_id=uuid.uuid4()
    )
    session.add_all([internal, external])
    session.flush()
    claims = ClaimsService(session)
    internal_claim, external_claim = claims.claim_pair(internal.id, external.id)
    result = MatchResult(
        run_id="review-test",
        match_type="FUZZY",
        status="PENDING_REVIEW",
        confidence=Decimal("0.877"),
        internal_transaction_id=internal.id,
        external_transaction_id=external.id,
        field_scores={},
        matched_fields={},
        hard_constraints_passed=True,
        candidate_count=1,
        rationale="seeded for review tests",
        matched_on_date=date(2026, 3, 15),
    )
    session.add(result)
    session.flush()
    claims.finalise(internal_claim, result.id)
    claims.finalise(external_claim, result.id)
    return internal, external, result
