# src/recon/api/v1/schemas/responses.py
"""Response and request schemas. Decimal amounts serialise as JSON
strings, so no money value passes through a float."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class TransactionOut(BaseModel):
    """A normalised transaction as shown next to an exception. Only the masked account is
    exposed, and narration has already been PAN-masked at ingestion."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    bank_code: str
    format_type: str
    source_line_no: int | None
    txn_id: str
    bank_ref: str | None
    normalised_reference: str | None
    amount: Decimal
    currency: str
    direction: str
    is_reversal: bool
    txn_date: date
    settlement_date: date | None
    txn_timestamp_utc: datetime
    counterparty_name: str | None
    counterparty_account_masked: str | None
    narration: str | None
    match_status: str
    ingestion_file_id: uuid.UUID


class ExceptionContext(BaseModel):
    """What an analyst needs to judge an exception: the records it concerns and every match
    that was proposed, made or rejected for them."""

    transactions: list[TransactionOut]
    matches: list[MatchResultOut]


class MeOut(BaseModel):
    name: str
    role: str


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


class DailyTrendPoint(BaseModel):
    day: date
    total_transactions: int
    matched_count: int
    match_rate: float
    exception_count: int


class DashboardSummary(BaseModel):
    as_of: date
    total_transactions: int
    matched_count: int
    match_rate: float
    match_rate_of_total: float  # same value as match_rate; kept for earlier clients
    avg_processing_time_seconds: float | None
    daily_trend: list[DailyTrendPoint]
    pending_review_count: int
    exception_count: int
    unresolved_count: int
    open_alert_count: int = 0
    sla_breached_open_count: int
    exceptions_by_category: dict[str, int]
    exceptions_by_severity: dict[str, int]


class BankDashboardRow(BaseModel):
    bank_code: str
    bank_name: str | None
    total_transactions: int
    matched_count: int
    match_rate: float
    exception_count: int
    open_exception_count: int
    exception_rate: float
    avg_settlement_days: float | None
    sla_compliance_percent: float


class BanksDashboard(BaseModel):
    banks: list[BankDashboardRow]


class TimelineHour(BaseModel):
    hour: int
    transaction_count: int
    matched_count: int
    match_rate: float


class TimelineDashboard(BaseModel):
    date: date
    total_transactions: int
    hours: list[TimelineHour]


class ReconcileRequest(BaseModel):
    bank_code: str = Field(pattern=r"^[A-Z0-9_]{2,20}$")
    date_from: date | None = Field(default=None, description="Inclusive txn_date lower bound.")
    date_to: date | None = Field(default=None, description="Inclusive txn_date upper bound.")
    mode: Literal["FAST", "FULL"] = Field(
        default="FULL", description="FAST runs exact matching only (B4.3 backlog mode)."
    )

    @model_validator(mode="after")
    def _range_is_ordered(self) -> ReconcileRequest:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bank_code: str
    status: str
    mode: str
    date_from: date | None
    date_to: date | None
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
    reviewed_by: str | None
    reviewed_at: datetime | None


class UploadOut(BaseModel):
    ingestion_file_id: uuid.UUID
    bank_code: str
    format_type: str
    source: str
    original_filename: str
    size_bytes: int
    content_sha256: str
    status: str
    record_count: int
    parsed_count: int
    error_count: int
    normalised_count: int
    normalisation_failed_count: int
    exceptions_created: int
    alerts_raised: int = 0
    pan_masked_count: int = 0
    statement_balanced: bool | None = None
    reconciliation_run_id: uuid.UUID | None = None


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    note: str | None = Field(default=None, max_length=2000)


class RejectRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=2000)


class FxRateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rate_date: date
    currency: str
    rate_inr: Decimal
    source: str
    suspect: bool
    suspect_reason: str | None


class FxUploadOut(BaseModel):
    inserted: int
    skipped_existing: int
    rejected_count: int
    rejected_sample: list[str]
    suspect_count: int
    suspect_sample: list[str]


class AssignRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    assignee: str = Field(min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=2000)


class SlaScanOut(BaseModel):
    escalated_count: int
    breached_at_top_tier_count: int


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    alert_type: str
    severity: str
    status: str
    bank_code: str | None
    route_to: str
    subject: str
    message: str
    details: dict[str, Any]
    run_id: str | None
    created_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None


class AcknowledgeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    note: str | None = Field(default=None, max_length=2000)


class AbsenceScanOut(BaseModel):
    alerts_raised: int


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bank_code: str
    format_type: str
    source: str | None
    original_filename: str
    content_sha256: str
    size_bytes: int
    status: str
    record_count: int
    parse_error_count: int
    ingested_at: datetime
    ingested_by: str
    integrity_status: str | None
    integrity_checked_at: datetime | None


class FileVerificationOut(BaseModel):
    ingestion_file_id: uuid.UUID
    status: str
    expected_sha256: str
    actual_sha256: str | None


class VerifyAllOut(BaseModel):
    checked: int
    ok: int
    tampered: int
    missing: int


class BankConfigOut(BaseModel):
    bank_code: str
    bank_name: str
    config_version: str
    content_sha256: str
    supported_formats: list[str]
    timezone: str
    settlement_cycle: str
    reconciliation_window_days: int
    recorded_in_database: bool


class BankConfigsOut(BaseModel):
    banks: list[BankConfigOut]
    loaded_at: datetime
    last_error: str | None
    recorded_versions: int


class ConfigVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bank_code: str
    config_version: str
    content_sha256: str
    is_active: bool
    loaded_by: str
    loaded_at: datetime


class ReloadOut(BaseModel):
    changed: bool
    added: list[str]
    updated: list[str]
    removed: list[str]
    error: str | None
    recorded: list[str]


class SampleReportOut(BaseModel):
    header: list[str]
    missing_columns: list[str]
    new_columns: list[str]
    reordered: bool
    rows_checked: int
    rows_valid: int
    problems: list[str]
    preview: list[dict[str, str]]


class ValidationReportOut(BaseModel):
    ok: bool
    bank_code: str | None
    config_version: str | None
    config_errors: list[str]
    warnings: list[str]
    sample: SampleReportOut | None


class CreateUserRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=2, max_length=100, pattern=r"^[A-Za-z0-9._@-]+$")
    email: str | None = Field(default=None, max_length=200)
    role: Literal["VIEWER", "ANALYST", "ADMIN", "SYSTEM"]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: str | None
    role: str
    is_active: bool
    created_at: datetime


class IssueKeyRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    label: str = Field(default="default", min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")


class ApiKeyIssuedOut(BaseModel):
    id: uuid.UUID
    name: str
    role: str
    api_key: str


class ReportTableOut(BaseModel):
    name: str
    columns: list[str]
    rows: list[list[str | int | float | bool | None]]


class ReportOut(BaseModel):
    kind: str
    title: str
    generated_at: datetime
    parameters: dict[str, Any]
    summary: dict[str, str | int | float | bool | None]
    tables: list[ReportTableOut]
    verdict: str | None
    notes: list[str]


class AnchorOut(BaseModel):
    chain_id: str
    sequence_no: int
    current_hash: str


class AnchorsOut(BaseModel):
    generated_at: datetime
    anchors: list[AnchorOut]


class ManualMatchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    internal_transaction_id: uuid.UUID
    external_transaction_id: uuid.UUID
    note: str = Field(min_length=1, max_length=2000, description="Why these two belong together.")


class UnmatchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=2000)


class UnmatchedOut(BaseModel):
    match_result_ids: list[uuid.UUID]
    status: str


class OverrideRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tier: int = Field(ge=1, le=4)
    reason: str = Field(min_length=1, max_length=2000)


class ReopenRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=2000)


class BacklogRequest(BaseModel):
    banks: list[str] | None = Field(
        default=None, description="Banks to recover; default every configured bank."
    )
    date_from: date | None = None
    date_to: date | None = None
    queue_full: bool = Field(
        default=True, description="Queue the full pipeline for what the fast pass leaves."
    )

    @model_validator(mode="after")
    def _ordered(self) -> BacklogRequest:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        return self


class BacklogDayOut(BaseModel):
    day: date
    run_id: uuid.UUID
    status: str
    exact_matched: int


class BacklogBankOut(BaseModel):
    bank_code: str
    days_processed: int
    exact_matched: int
    failed_days: list[date]
    days: list[BacklogDayOut]
    queued_full_run_id: uuid.UUID | None


class BacklogOut(BaseModel):
    banks: list[BacklogBankOut]
    queued_full_runs: int
    recovery: dict[str, str | int | float | bool | None]
