# src/recon/persistence/models/__init__.py
"""ORM model registry.

Importing this module registers every table on Base.metadata, which is
what migrations/env.py's target_metadata relies on for `alembic check`
and for autogenerate against any future non-partitioned table.
"""

from __future__ import annotations

from recon.persistence.models.adjustments import LedgerAdjustment
from recon.persistence.models.alerts import Alert, CounterpartyStat
from recon.persistence.models.audit import AuditLog
from recon.persistence.models.base import Base
from recon.persistence.models.config_store import BankConfiguration
from recon.persistence.models.exceptions import ExceptionEvent, ReconException
from recon.persistence.models.fx import FxRate
from recon.persistence.models.ingestion import IngestionFile, RawTransaction
from recon.persistence.models.match_results import MatchResult
from recon.persistence.models.matching import MatchClaim
from recon.persistence.models.normalised import NormalisedTransaction
from recon.persistence.models.runs import ReconciliationRun
from recon.persistence.models.security import ApiKey, User
from recon.persistence.models.statements import StatementBalance

__all__ = [
    "Alert",
    "ApiKey",
    "AuditLog",
    "BankConfiguration",
    "Base",
    "CounterpartyStat",
    "ExceptionEvent",
    "FxRate",
    "IngestionFile",
    "LedgerAdjustment",
    "MatchClaim",
    "MatchResult",
    "NormalisedTransaction",
    "RawTransaction",
    "ReconException",
    "ReconciliationRun",
    "StatementBalance",
    "User",
]
