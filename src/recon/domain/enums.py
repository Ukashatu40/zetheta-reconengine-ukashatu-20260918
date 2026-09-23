# src/recon/domain/enums.py
"""Enumerations shared across the domain, ingestion, matching and exception
layers.

Several of these encode corrections from docs/ASSESSMENT_ERRORS_AND_CORRECTIONS.md
rather than the PDF's literal wording; each is annotated with which
correction it implements.
"""

from __future__ import annotations

from enum import Enum, StrEnum


class Direction(StrEnum):
    """Debit/credit direction.

    AE-12: the PDF's canonical model (A2.4) defines this as ENUM(DR, CR)
    even though A2.1 itself documents MT940's :61: mark as taking D, C, RD
    or RC (reversal variants). Rather than expand this enum to four values
    (which would make every direction-equality check in the matching engine
    need to know about reversal semantics), DR/CR is kept exactly as
    specified and reversal is carried as a separate boolean on
    CanonicalTransaction (`is_reversal`). A reversed debit is still,
    fundamentally, a credit-direction movement of money; `is_reversal` is
    what distinguishes it from an originally-credit entry.
    """

    DR = "DR"
    CR = "CR"


class TransactionSource(StrEnum):
    """Which side of the reconciliation a normalised transaction came from."""

    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


class SourceFormat(StrEnum):
    """The wire format a raw transaction was ingested from."""

    CSV = "CSV"
    MT940 = "MT940"
    CAMT053 = "CAMT053"
    INTERNAL_LEDGER = "INTERNAL_LEDGER"


class CurrencySource(StrEnum):
    """Where a normalised transaction's currency value was resolved from.

    AE-13: A2.4 sources `currency` from MT940's statement-level :60F:, which
    conflicts with Day 2's requirement to handle multi-currency statements
    (where individual :61: lines carry their own currency). Recording the
    resolution path makes that ambiguity auditable per-record instead of
    silently picking one source.
    """

    ENTRY_LEVEL = "ENTRY_LEVEL"  # :61: sub-field or CAMT Ntry/Amt/@Ccy
    STATEMENT_LEVEL = "STATEMENT_LEVEL"  # :60F: or Stmt-level default
    BANK_CONFIG_DEFAULT = "BANK_CONFIG_DEFAULT"


class MatchType(StrEnum):
    EXACT = "EXACT"
    FUZZY = "FUZZY"
    RULE = "RULE"
    SPLIT = "SPLIT"
    NETTED = "NETTED"
    CROSS_CURRENCY = "CROSS_CURRENCY"
    MANUAL = "MANUAL"


class MatchStatus(StrEnum):
    """Lifecycle status of a match_results row.

    AE-10: Day 3 instructs writing every match scoring above 0.60 into
    match_results as though it were a completed match, which contradicts
    A3.4's own 0.60-0.85 human-review band. This status field is what makes
    both instructions true simultaneously: the row is written (satisfying
    Day 3 literally) but PENDING_REVIEW rows are excluded from match-rate
    and reconciled-value metrics until a human confirms them (satisfying
    A3.4 substantively).
    """

    AUTO_MATCHED = "AUTO_MATCHED"
    PENDING_REVIEW = "PENDING_REVIEW"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class ExceptionCategory(StrEnum):
    """The 18 categories from PDF A4.1, plus SETTLEMENT_DELAY from A7.1
    Clause 8.2, which the PDF itself suggests adding "beyond the 18 listed".
    """

    MISSING_INTERNAL = "MISSING_INTERNAL"
    MISSING_EXTERNAL = "MISSING_EXTERNAL"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    DUPLICATE_INTERNAL = "DUPLICATE_INTERNAL"
    DUPLICATE_EXTERNAL = "DUPLICATE_EXTERNAL"
    DATE_MISMATCH = "DATE_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    DIRECTION_REVERSAL = "DIRECTION_REVERSAL"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    NETTED_SETTLEMENT = "NETTED_SETTLEMENT"
    FEE_DEDUCTION = "FEE_DEDUCTION"
    FX_VARIANCE = "FX_VARIANCE"
    STALE_TRANSACTION = "STALE_TRANSACTION"
    FORMAT_ERROR = "FORMAT_ERROR"
    REFERENCE_TRUNCATED = "REFERENCE_TRUNCATED"
    TIMEZONE_OFFSET = "TIMEZONE_OFFSET"
    REVERSAL_PENDING = "REVERSAL_PENDING"
    REGULATORY_HOLD = "REGULATORY_HOLD"
    SETTLEMENT_DELAY = "SETTLEMENT_DELAY"  # [ENGINEERING DECISION] per A7.1


class ExceptionSeverity(StrEnum):
    """AE-07: severity is computed independently of SLA (max of a category
    base severity and a value-derived severity), not derived circularly
    from the SLA duration as Day 4 describes it.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ExceptionStatus(StrEnum):
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    WRITTEN_OFF = "WRITTEN_OFF"
    REOPENED = "REOPENED"


class EscalationTier(int, Enum):
    """PDF A4.2 four-tier model."""

    TIER_1_AUTO = 1
    TIER_2_ANALYST = 2
    TIER_3_SENIOR = 3
    TIER_4_COMPLIANCE = 4


class RunState(StrEnum):
    """Reconciliation run state machine (see docs/ARCHITECTURE.md).

    A run can only report COMPLETED after passing through every stage in
    order; a crash mid-run leaves it in a resumable, non-terminal state,
    never silently marked as though it finished.
    """

    PENDING = "PENDING"
    INGESTING = "INGESTING"
    NORMALISING = "NORMALISING"
    MATCHING_EXACT = "MATCHING_EXACT"
    MATCHING_FUZZY = "MATCHING_FUZZY"
    MATCHING_RULES = "MATCHING_RULES"
    CLASSIFYING = "CLASSIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    RECOVERING = "RECOVERING"


class RunMode(StrEnum):
    """B4.3 recovery scenario: FAST is exact-only for rapid backlog
    reduction; FULL runs the complete matching hierarchy."""

    FAST = "FAST"
    FULL = "FULL"


class AuditActionType(StrEnum):
    """PDF A4.3: INGEST, NORMALISE, MATCH, UNMATCH, RESOLVE, ESCALATE,
    OVERRIDE — extended with a couple of actions the PDF's list implies but
    doesn't name."""

    INGEST = "INGEST"
    NORMALISE = "NORMALISE"
    MATCH = "MATCH"
    UNMATCH = "UNMATCH"
    RESOLVE = "RESOLVE"
    ESCALATE = "ESCALATE"
    OVERRIDE = "OVERRIDE"
    RUN_STATE_CHANGE = "RUN_STATE_CHANGE"
    CONFIG_CHANGE = "CONFIG_CHANGE"
