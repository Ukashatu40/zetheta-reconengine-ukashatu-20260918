# src/recon/api/v1/schemas/responses.py
"""Response and request schemas. Decimal amounts serialise as JSON
strings, so no money value passes through a float."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: str
    severity: str
    status: str
    assigned_tier: int
    assigned_to: str | None
    bank_code: str
    run_id: str | None
    affected_records: dict[str, Any]
    financial_impact_amount: Decimal | None
    financial_impact_currency: str | None
    suggested_resolution: str
    rationale: str
    created_at: datetime
    sla_deadline: datetime
    sla_breached_at: datetime | None
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_details: str | None


class ExceptionEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    from_tier: int | None
    to_tier: int | None
    actor: str
    detail: str
    occurred_at: datetime


class ExceptionDetail(ExceptionOut):
    events: list[ExceptionEventOut]


class ResolveRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    resolution_details: str = Field(min_length=1, max_length=2000)
    outcome: Literal["RESOLVED", "WRITTEN_OFF"] = "RESOLVED"


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: uuid.UUID
    chain_id: str
    sequence_no: int
    occurred_at: datetime
    recorded_at: datetime
    actor_type: str
    actor_id: str
    action_type: str
    affected_records: dict[str, Any]
    before_state: dict[str, Any] | None
    after_state: dict[str, Any] | None
    rationale: str
    prev_hash: str | None
    current_hash: str


class ChainVerificationOut(BaseModel):
    ok: bool
    entries_checked: int
    failure_kind: str | None
    failure_sequence_no: int | None
    detail: str


class VerifyOut(BaseModel):
    ok: bool
    chains: dict[str, ChainVerificationOut]


class DashboardSummary(BaseModel):
    total_transactions: int
    matched_count: int
    match_rate_of_total: float
    pending_review_count: int
    exception_count: int
    unresolved_count: int
    sla_breached_open_count: int
    exceptions_by_category: dict[str, int]
    exceptions_by_severity: dict[str, int]


class ReconcileRequest(BaseModel):
    bank_code: str = Field(pattern=r"^[A-Z0-9_]{2,20}$")


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bank_code: str
    status: str
    requested_by: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    metrics: dict[str, Any] | None
    error_summary: str | None


class MatchResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    match_type: str
    status: str
    confidence: Decimal
    internal_transaction_id: uuid.UUID
    external_transaction_id: uuid.UUID
    field_scores: dict[str, Any]
    matched_fields: dict[str, Any]
    hard_constraints_passed: bool
    rule_id: str | None
    candidate_count: int
    rationale: str
    matched_on_date: date
    created_at: datetime
    weights_version: str | None
