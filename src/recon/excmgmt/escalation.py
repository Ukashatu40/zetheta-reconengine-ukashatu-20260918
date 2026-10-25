# src/recon/excmgmt/escalation.py
"""EscalationManager (Day 4, A4.2): after exceptions are created it

  1. auto-resolves the Tier 1 ones (AutoResolver),
  2. routes every remaining open exception to its tier's queue,
  3. escalates any exception whose SLA has passed (SlaScanner).

Tier 3 and Tier 4 placement on value, FX variance and systemic failure is decided
when the exception is created (routing.route); this class acts on that decision
and on the passage of time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from recon.audit.logger import AuditLogger
from recon.excmgmt.autoresolve.resolver import AutoResolveOutcome, AutoResolver
from recon.excmgmt.queues import QUEUE_BY_TIER
from recon.excmgmt.sla import SlaScanner, SlaScanOutcome
from recon.persistence.models import ExceptionEvent, ReconException

_ACTOR = "system:escalation-manager"
_OPEN = ("OPEN", "IN_REVIEW", "ESCALATED", "REOPENED")


@dataclass(frozen=True, slots=True)
class EscalationOutcome:
    auto_resolve: AutoResolveOutcome
    queued_count: int
    sla: SlaScanOutcome


class EscalationManager:
    def __init__(self, session: Session, audit: AuditLogger) -> None:
        self._session = session
        self._audit = audit

    def process(
        self, bank_code: str, now: datetime, run_id: str | None = None
    ) -> EscalationOutcome:
        auto = AutoResolver(self._session, self._audit).run(bank_code, now, run_id)
        queued = self._assign_queues(bank_code, now)
        sla = SlaScanner(self._session, self._audit).scan(now)
        return EscalationOutcome(auto, queued, sla)

    def _assign_queues(self, bank_code: str, now: datetime) -> int:
        """Gives every unassigned open exception the queue for its tier. A person
        already assigned is never overwritten."""
        total = 0
        for tier, queue in QUEUE_BY_TIER.items():
            ids = list(
                self._session.scalars(
                    select(ReconException.id).where(
                        ReconException.bank_code == bank_code,
                        ReconException.status.in_(_OPEN),
                        ReconException.assigned_tier == tier,
                        ReconException.assigned_to.is_(None),
                    )
                )
            )
            if not ids:
                continue
            self._session.execute(
                update(ReconException)
                .where(ReconException.id.in_(ids))
                .values(assigned_to=queue)
                .execution_options(synchronize_session=False)
            )
            self._session.add_all(
                ExceptionEvent(
                    exception_id=exception_id,
                    event_type="ASSIGNED",
                    from_tier=tier,
                    to_tier=tier,
                    actor=_ACTOR,
                    detail=f"assigned to {queue}",
                    occurred_at=now,
                )
                for exception_id in ids
            )
            total += len(ids)
        self._session.flush()
        return total
