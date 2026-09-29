# src/recon/persistence/models/match_results.py
"""ORM model for match_results.

Every field here maps directly to the explainable-match-result shape
described in PDF A3.4 and the Phase 0 report's Section 9.5: match_method,
confidence, field_scores, matched_fields, rule_applied, candidate_count,
rationale, source_record_ids.

status follows AE-10 (docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md): a row
scoring in the 0.60-0.85 review band is still written (satisfying Day
3's literal instruction) but excluded from match-rate metrics via its
status, not its mere presence in the table (satisfying A3.4
substantively). Exact matches always land at AUTO_MATCHED with confidence
1.00 — there is no ambiguity band for an exact hash match by definition.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from recon.persistence.models.base import Base


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    run_id: Mapped[str] = mapped_column(String(100), nullable=False)
    match_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    weights_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    internal_transaction_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    external_transaction_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )

    field_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    matched_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    hard_constraints_passed: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    rule_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    matched_on_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            """match_type IN
            ('EXACT', 'FUZZY', 'RULE', 'SPLIT', 'NETTED', 'CROSS_CURRENCY', 'MANUAL')""",
            name="match_type_valid",
        ),
        CheckConstraint(
            "status IN ('AUTO_MATCHED', 'PENDING_REVIEW', 'CONFIRMED', 'REJECTED')",
            name="status_valid",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        # AE-10 enforced at the database level, not just by application
        # discipline: an AUTO_MATCHED row must have crossed the auto-match
        # threshold. This mirrors the reasoning behind match_claims'
        # partial unique index — push the safety property into the
        # schema, don't just trust the code that writes it.
        CheckConstraint(
            "status != 'AUTO_MATCHED' OR confidence > 0.85",
            name="auto_matched_requires_high_confidence",
        ),
    )
