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

import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from recon.anomaly.service import AnomalyService
from recon.audit.chains import MATCHING_CHAIN, RUNS_CHAIN
from recon.audit.logger import AuditLogger
from recon.config.registry import get_registry
from recon.config.store import sync_bank_configurations
from recon.domain.enums import AuditActionType
from recon.excmgmt.classifier import ClassifierOutcome, ExceptionClassifier
from recon.excmgmt.escalation import EscalationManager, EscalationOutcome
from recon.fx.service import FxService
from recon.matching.orchestrator import MatchingOrchestrator, OrchestratorOutcome
from recon.matching.stage_config import build_rule_stage
from recon.persistence.models import MatchResult, ReconciliationRun
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


def _audit_safe(value: Any) -> Any:
    """The audit trail refuses floats (they do not round-trip exactly), so rates and
    durations are recorded as strings."""
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {k: _audit_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_audit_safe(v) for v in value]
    return value


class RunService:
    def __init__(
        self, session: Session, settings: RunSettings, clock: Callable[[], datetime]
    ) -> None:
        self._session = session
        self._settings = settings
        self._clock = clock

    def start(
        self,
        *,
        bank_code: str,
        idempotency_key: str,
        requested_by: str,
        mode: str = "FULL",
        date_from: date | None = None,
        date_to: date | None = None,
        defer: bool = False,
    ) -> StartResult:
        """Creates the run RUNNING, or PENDING when `defer` is set (a background job that
        activate() starts later). A PENDING run does not hold the one-running-per-bank slot."""
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ValueError("date_from must not be after date_to")
        request = (bank_code, mode, date_from, date_to)
        existing = self._by_key(idempotency_key)
        if existing is not None:
            return self._replay(existing, request)

        now = self._clock()
        self._reap_stale(bank_code, now)
        run = ReconciliationRun(
            id=uuid.uuid4(),
            idempotency_key=idempotency_key,
            bank_code=bank_code,
            status="PENDING" if defer else "RUNNING",
            mode=mode,
            date_from=date_from,
            date_to=date_to,
            requested_by=requested_by,
            created_at=now,
            started_at=None if defer else now,
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
                    return self._replay(winner, request)
            if name == _ONE_RUNNING_INDEX:
                raise RunAlreadyActiveError(
                    f"a run for {bank_code} is already in progress"
                ) from exc
            raise
        self._audit_state(
            run,
            "NONE",
            run.status,
            rationale=f"requested by {requested_by}" + (" (queued)" if defer else ""),
            at=now,
        )
        return StartResult(run, created=True)

    def activate(self, run_id: uuid.UUID) -> None:
        """PENDING -> RUNNING. Raises RunAlreadyActiveError if the bank has a run in progress."""
        run = self._session.get(ReconciliationRun, run_id, with_for_update=True)
        if run is None or run.status != "PENDING":
            raise RunStateError(f"run {run_id} is not PENDING")
        now = self._clock()
        self._reap_stale(run.bank_code, now)
        try:
            with self._session.begin_nested():  # a conflict leaves the run PENDING, as it was
                run.status = "RUNNING"
                run.started_at = now
                self._session.flush()
        except IntegrityError as exc:
            if _constraint_name(exc) == _ONE_RUNNING_INDEX:
                raise RunAlreadyActiveError(
                    f"a run for {run.bank_code} is already in progress"
                ) from exc
            raise
        self._audit_state(run, "PENDING", "RUNNING", rationale="queued run started", at=now)

    def execute(self, run_id: uuid.UUID) -> None:
        run = self._session.get(ReconciliationRun, run_id)
        if run is None or run.status != "RUNNING":
            raise RunStateError(f"run {run_id} is not RUNNING")
        bank_code = run.bank_code
        stale_days = self._settings.stale_after_days(bank_code)
        now = self._clock()
        audit = AuditLogger(self._session)
        fx = FxService(self._session)
        registry = get_registry()
        live = registry.loaded().get(bank_code)
        try:
            # Record the configuration this run uses (hot-swapped or not) before using it.
            sync_bank_configurations(
                self._session, registry, loaded_by="system:run-service", now=now
            )
            outcome = MatchingOrchestrator(
                self._session,
                str(run_id),
                self._settings.blocking_for(bank_code),
                self._settings.amount_tolerance_for(bank_code),
                self._settings.matching,
                set_based_exact=self._settings.set_based_exact,
                mode=run.mode,
                date_from=run.date_from,
                date_to=run.date_to,
                rules=build_rule_stage(self._settings.banks.get(bank_code)),
                fx=fx,
                audit=audit,
                now=now,
            ).run(bank_code)
            self._audit_matches(run, outcome, now)
            classified = ExceptionClassifier(
                self._session,
                self._settings.taxonomy,
                audit,
                banks=self._settings.banks,
                fx=fx,
            ).classify_run(
                bank_code,
                str(run_id),
                now,
                as_of_date=now.date() if stale_days is not None else None,
                stale_after_days=stale_days,
                date_from=run.date_from,
                date_to=run.date_to,
            )
            escalation = EscalationManager(self._session, audit).process(
                bank_code, now, str(run_id)
            )
        except Exception as exc:
            logger.exception("reconciliation run %s failed", run_id)
            self._session.rollback()  # discards matching and classification work only
            self._finish(run_id, "FAILED", metrics=None, error_summary=type(exc).__name__)
            return
        metrics = self._metrics(outcome, classified, escalation) | {
            "mode": run.mode,
            "date_from": run.date_from.isoformat() if run.date_from else None,
            "date_to": run.date_to.isoformat() if run.date_to else None,
        }
        if live is not None:
            metrics["bank_config"] = {
                "version": live.config.config_version,
                "sha256": live.sha256,
            }
        metrics["alerts_raised"] = AnomalyService(
            self._session, self._settings.anomaly, self._settings.banks
        ).evaluate_run(run_id=str(run_id), bank_code=bank_code, metrics=metrics, now=now)
        self._finish(run_id, "COMPLETED", metrics=metrics, error_summary=None)

    # -- internals -----------------------------------------------------

    def _by_key(self, key: str) -> ReconciliationRun | None:
        return self._session.scalars(
            select(ReconciliationRun).where(ReconciliationRun.idempotency_key == key)
        ).first()

    @staticmethod
    def _replay(
        existing: ReconciliationRun, request: tuple[str, str, date | None, date | None]
    ) -> StartResult:
        if (existing.bank_code, existing.mode, existing.date_from, existing.date_to) != request:
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
        previous = run.status
        run.status = status
        run.completed_at = self._clock()
        run.metrics = metrics
        run.error_summary = error_summary
        self._session.flush()
        self._audit_state(
            run,
            previous,
            status,
            rationale=error_summary or "run completed",
            at=run.completed_at,
            metrics=metrics,
        )

    def _audit_state(
        self,
        run: ReconciliationRun,
        before: str,
        after: str,
        *,
        rationale: str,
        at: datetime,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """A4.3 RUN_STATE_CHANGE: every transition of a run is on the audit chain."""
        requested = before == "NONE"  # the creation of the run is the person's act
        AuditLogger(self._session).append(
            chain_id=RUNS_CHAIN,
            actor_type="USER" if requested else "SYSTEM",
            actor_id=f"api-key:{run.requested_by}" if requested else "run-service",
            action_type=AuditActionType.RUN_STATE_CHANGE,
            occurred_at=at,
            affected_records={"run_id": str(run.id), "bank_code": run.bank_code},
            before_state={"status": before},
            after_state={
                "status": after,
                "mode": run.mode,
                **({"metrics": _audit_safe(metrics)} if metrics else {}),
            },
            rationale=rationale,
        )

    def _audit_matches(
        self, run: ReconciliationRun, outcome: OrchestratorOutcome, now: datetime
    ) -> None:
        """One MATCH entry per run. Group and cross-currency matches are audited
        one by one as they are made (they need their rate and sum in the trail);
        every other match is covered here by count and by a SHA-256 over the
        sorted ids of all of the run's match results, so the set cannot change
        unnoticed (A7.2: a reproducible, verifiable run)."""
        ids = [
            str(i)
            for i in self._session.scalars(
                select(MatchResult.id)
                .where(MatchResult.run_id == str(run.id))
                .order_by(MatchResult.id)
            )
        ]
        by_type: dict[str, int] = {
            row[0]: row[1]
            for row in self._session.execute(
                select(MatchResult.match_type, func.count())
                .where(MatchResult.run_id == str(run.id))
                .group_by(MatchResult.match_type)
            )
        }
        AuditLogger(self._session).append(
            chain_id=MATCHING_CHAIN,
            actor_type="SYSTEM",
            actor_id="run-service",
            action_type=AuditActionType.MATCH,
            occurred_at=now,
            affected_records={"run_id": str(run.id), "bank_code": run.bank_code},
            after_state={
                "match_result_count": len(ids),
                "by_match_type": by_type,
                "match_result_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
                "externals_matched": outcome.externals_matched,
                "pending_review": outcome.total_pending_review_count,
            },
            rationale=f"run {run.id} produced {len(ids)} match results ({run.mode})",
        )

    def _metrics(
        self,
        outcome: OrchestratorOutcome,
        classified: ClassifierOutcome,
        escalation: EscalationOutcome,
    ) -> dict[str, Any]:
        m = outcome.metrics
        return {
            "exact_matched": outcome.exact.matched_count,
            "fuzzy_auto_matched": outcome.fuzzy.auto_matched_count,
            "pending_review": outcome.total_pending_review_count,
            "rule_matched": outcome.rule.auto_matched_count if outcome.rule else 0,
            "cross_currency_matched": (
                outcome.cross_currency.matched_count if outcome.cross_currency else 0
            ),
            "cross_currency_out_of_tolerance": (
                outcome.cross_currency.out_of_tolerance_count if outcome.cross_currency else 0
            ),
            "split_groups": outcome.group.split_groups if outcome.group else 0,
            "netted_groups": outcome.group.netted_groups if outcome.group else 0,
            "group_ambiguous": outcome.group.ambiguous_count if outcome.group else 0,
            "group_too_complex": outcome.group.too_complex_count if outcome.group else 0,
            "group_missing_fx": outcome.group.missing_fx_count if outcome.group else 0,
            "externals_matched": outcome.externals_matched,
            "internal_pool": outcome.internal_pool_size_before_exact,
            "external_pool": outcome.external_pool_size_before_exact,
            "exact_match_rate_of_total": m.exact_match_rate_of_total,
            "exact_match_rate_of_matchable": m.exact_match_rate_of_matchable,
            "overall_match_rate_of_total": m.overall_match_rate_of_total,
            "duration_seconds": {
                "exact": m.exact_duration_seconds,
                "fuzzy": m.fuzzy_duration_seconds,
                "rules": outcome.rule_duration_seconds,
                "total": m.total_duration_seconds,
            },
            "exceptions_created": classified.created_count,
            "exceptions_by_category": classified.created_by_category,
            "exceptions_auto_resolved": escalation.auto_resolve.resolved_count,
            "exceptions_auto_resolve_escalated": escalation.auto_resolve.escalated_count,
            "exceptions_queued": escalation.queued_count,
            "sla_escalations": escalation.sla.escalated_count,
            "weights_version": self._settings.matching.config_version,
        }
