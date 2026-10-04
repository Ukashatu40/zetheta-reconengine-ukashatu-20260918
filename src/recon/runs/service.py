# src/recon/runs/service.py
"""RunService: starts and executes reconciliation runs.

State machine: RUNNING -> COMPLETED | FAILED. The database allows at most one
RUNNING run per bank (partial unique index) and one run per idempotency key.

start() does not commit. The caller commits so the RUNNING row is visible to
other connections BEFORE the work begins; that is what makes the per-bank
guard work across requests.

execute() runs matching then exception classification in the caller's
session. If either raises, ALL of that work is rolled back and the run is
marked FAILED with only the exception class name (the detail goes to the
log, never to the API).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recon.audit.logger import AuditLogger
from recon.excmgmt.classifier import ClassifierOutcome, ExceptionClassifier
from recon.matching.orchestrator import MatchingOrchestrator, OrchestratorOutcome
from recon.persistence.models import ReconciliationRun
from recon.runs.settings import RunSettings

logger = logging.getLogger("recon.runs")

_IDEMPOTENCY_CONSTRAINT = "uq_reconciliation_runs_idempotency_key"
_ONE_RUNNING_INDEX = "ux_reconciliation_runs_one_running_per_bank"


class IdempotencyConflictError(ValueError):
    """The idempotency key was already used for a different request."""


class RunAlreadyActiveError(RuntimeError):
    """A run for this bank is already in progress."""


class RunStateError(RuntimeError):
    """The run is not in a state that allows this transition."""


@dataclass(frozen=True, slots=True)
class StartResult:
    run: ReconciliationRun
    created: bool


def _constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


class RunService:
    def __init__(
        self, session: Session, settings: RunSettings, clock: Callable[[], datetime]
    ) -> None:
        self._session = session
        self._settings = settings
        self._clock = clock

    def start(self, *, bank_code: str, idempotency_key: str, requested_by: str) -> StartResult:
        existing = self._by_key(idempotency_key)
        if existing is not None:
            return self._replay(existing, bank_code)

        now = self._clock()
        self._reap_stale(bank_code, now)
        run = ReconciliationRun(
            id=uuid.uuid4(),
            idempotency_key=idempotency_key,
            bank_code=bank_code,
            status="RUNNING",
            requested_by=requested_by,
            created_at=now,
            started_at=now,
        )
        try:
            with self._session.begin_nested():
                self._session.add(run)
                self._session.flush()
        except IntegrityError as exc:
            name = _constraint_name(exc)
            if name == _IDEMPOTENCY_CONSTRAINT:  # a concurrent request with the same key won
                winner = self._by_key(idempotency_key)
                if winner is not None:
                    return self._replay(winner, bank_code)
            if name == _ONE_RUNNING_INDEX:
                raise RunAlreadyActiveError(
                    f"a run for {bank_code} is already in progress"
                ) from exc
            raise
        return StartResult(run, created=True)

    def execute(self, run_id: uuid.UUID) -> None:
        run = self._session.get(ReconciliationRun, run_id)
        if run is None or run.status != "RUNNING":
            raise RunStateError(f"run {run_id} is not RUNNING")
        bank_code = run.bank_code
        try:
            outcome = MatchingOrchestrator(
                self._session,
                str(run_id),
                self._settings.blocking,
                self._settings.amount_tolerance_minor,
                self._settings.matching,
            ).run(bank_code)
            classified = ExceptionClassifier(
                self._session, self._settings.taxonomy, AuditLogger(self._session)
            ).classify_unmatched(bank_code, str(run_id), self._clock())
        except Exception as exc:
            logger.exception("reconciliation run %s failed", run_id)
            self._session.rollback()  # discards matching and classification work only
            self._finish(run_id, "FAILED", metrics=None, error_summary=type(exc).__name__)
            return
        self._finish(
            run_id, "COMPLETED", metrics=self._metrics(outcome, classified), error_summary=None
        )

    # -- internals -----------------------------------------------------

    def _by_key(self, key: str) -> ReconciliationRun | None:
        return self._session.scalars(
            select(ReconciliationRun).where(ReconciliationRun.idempotency_key == key)
        ).first()

    @staticmethod
    def _replay(existing: ReconciliationRun, bank_code: str) -> StartResult:
        if existing.bank_code != bank_code:
            raise IdempotencyConflictError(
                "idempotency key was already used for a different request"
            )
        return StartResult(existing, created=False)

    def _reap_stale(self, bank_code: str, now: datetime) -> None:
        self._session.execute(
            update(ReconciliationRun)
            .where(
                ReconciliationRun.bank_code == bank_code,
                ReconciliationRun.status == "RUNNING",
                ReconciliationRun.started_at < now - self._settings.stale_run_after,
            )
            .values(
                status="FAILED",
                completed_at=now,
                error_summary="reaped: exceeded the stale-run window",
            )
            .execution_options(synchronize_session=False)
        )

    def _finish(
        self,
        run_id: uuid.UUID,
        status: str,
        *,
        metrics: dict[str, Any] | None,
        error_summary: str | None,
    ) -> None:
        run = self._session.get(ReconciliationRun, run_id)
        if run is None:  # unreachable: the RUNNING row was committed before execute()
            raise RunStateError(f"run {run_id} disappeared")
        run.status = status
        run.completed_at = self._clock()
        run.metrics = metrics
        run.error_summary = error_summary
        self._session.flush()

    def _metrics(
        self, outcome: OrchestratorOutcome, classified: ClassifierOutcome
    ) -> dict[str, Any]:
        m = outcome.metrics
        return {
            "exact_matched": outcome.exact.matched_count,
            "fuzzy_auto_matched": outcome.fuzzy.auto_matched_count,
            "pending_review": outcome.fuzzy.review_count,
            "internal_pool": outcome.internal_pool_size_before_exact,
            "external_pool": outcome.external_pool_size_before_exact,
            "exact_match_rate_of_total": m.exact_match_rate_of_total,
            "exact_match_rate_of_matchable": m.exact_match_rate_of_matchable,
            "overall_match_rate_of_total": m.overall_match_rate_of_total,
            "duration_seconds": {
                "exact": m.exact_duration_seconds,
                "fuzzy": m.fuzzy_duration_seconds,
                "total": m.total_duration_seconds,
            },
            "exceptions_created": classified.created_count,
            "exceptions_by_category": classified.created_by_category,
            "weights_version": self._settings.matching.config_version,
        }
