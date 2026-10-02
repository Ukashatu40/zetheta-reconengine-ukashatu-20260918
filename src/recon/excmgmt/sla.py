# src/recon/excmgmt/sla.py
"""SLA scanner (R66). An exception breaches once: the tier goes up by one
(capped at 4) and sla_breached_at is stamped, so a later scan does not
escalate it again. A breach at Tier 4 records an SLA_BREACHED event with no
tier change. The event rows are the simulated notifications.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from recon.domain.enums import EscalationTier
from recon.excmgmt.routing import tier_after_sla_check
from recon.persistence.models import ExceptionEvent, ReconException

_OPEN_STATUSES = ("OPEN", "IN_REVIEW", "ESCALATED", "REOPENED")
_ACTOR = "system:sla-scanner"


@dataclass(frozen=True, slots=True)
class SlaScanOutcome:
    escalated_count: int
    breached_at_top_tier_count: int


class SlaScanner:
    def __init__(self, session: Session) -> None:
        self._session = session

    def scan(self, now: datetime) -> SlaScanOutcome:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        due = (
            self._session.query(ReconException)
            .filter(
                ReconException.status.in_(_OPEN_STATUSES),
                ReconException.sla_breached_at.is_(None),
                ReconException.sla_deadline < now,
            )
            .order_by(ReconException.sla_deadline, ReconException.id)
            .with_for_update(skip_locked=True)
            .all()
        )
        escalated = top_tier = 0
        for exception in due:
            current = EscalationTier(exception.assigned_tier)
            new = tier_after_sla_check(current, exception.sla_deadline, now)
            exception.sla_breached_at = now
            if new == current:
                top_tier += 1
                event_type, detail = "SLA_BREACHED", "SLA deadline passed at the highest tier."
            else:
                escalated += 1
                exception.assigned_tier = new.value
                exception.status = "ESCALATED"
                event_type, detail = "ESCALATED", f"SLA breached; escalated to tier {new.value}."
            self._session.add(
                ExceptionEvent(
                    exception_id=exception.id,
                    event_type=event_type,
                    from_tier=current.value,
                    to_tier=new.value,
                    actor=_ACTOR,
                    detail=detail,
                    occurred_at=now,
                )
            )
        self._session.flush()
        return SlaScanOutcome(escalated, top_tier)
