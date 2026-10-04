# src/recon/persistence/models/__init__.py
"""ORM model registry.

Importing this module registers every table on Base.metadata, which is
what migrations/env.py's target_metadata relies on for `alembic check`
and for autogenerate against any future non-partitioned table.
"""

from __future__ import annotations

from recon.persistence.models.audit import AuditLog
from recon.persistence.models.base import Base
from recon.persistence.models.exceptions import ExceptionEvent, ReconException
from recon.persistence.models.ingestion import IngestionFile, RawTransaction
from recon.persistence.models.match_results import MatchResult
from recon.persistence.models.matching import MatchClaim
from recon.persistence.models.normalised import NormalisedTransaction
from recon.persistence.models.runs import ReconciliationRun
from recon.persistence.models.security import ApiKey

__all__ = [
    "ApiKey",
    "AuditLog",
    "Base",
    "ExceptionEvent",
    "IngestionFile",
    "MatchClaim",
    "MatchResult",
    "NormalisedTransaction",
    "RawTransaction",
    "ReconException",
    "ReconciliationRun",
]
