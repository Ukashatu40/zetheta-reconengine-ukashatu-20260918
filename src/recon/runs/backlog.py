# src/recon/runs/backlog.py
"""Backlog recovery (B4.3, "The Reconciliation Debt Crisis").

After an outage there can be millions of unmatched transactions. This service:

  1. prioritises the most recent day first (the T+1 regulatory deadline),
  2. runs banks in PARALLEL (each in its own session; a bank's days stay serial
     because only one run per bank may be active),
  3. uses FAST mode (exact matching only) for that first pass so the pool shrinks
     quickly and a preliminary report can go to the CFO,
  4. queues the full matching pipeline for what is left as a background job
     (a PENDING FULL run per bank, started after the response), and
  5. is idempotent: every run carries a key derived from the batch's idempotency
     key, so replaying the request reuses the runs instead of repeating them.

The session factory and the parallel map are injected so tests run it serially
against one database session.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.persistence.models import MatchClaim, NormalisedTransaction, ReconciliationRun
from recon.runs.service import RunAlreadyActiveError, RunService
from recon.runs.settings import RunSettings

logger = logging.getLogger("recon.backlog")

SessionFactory = Callable[[], AbstractContextManager[Session]]
ParallelMap = Callable[
    [Callable[[str], "BankBacklogResult"], list[str]], Iterable["BankBacklogResult"]
]


@dataclass(frozen=True, slots=True)
class DayRun:
    day: date
    run_id: uuid.UUID
    status: str
    exact_matched: int


@dataclass(slots=True)
class BankBacklogResult:
    bank_code: str
    days: list[DayRun] = field(default_factory=list)
    failed_days: list[date] = field(default_factory=list)
    queued_full_run_id: uuid.UUID | None = None

    @property
    def exact_matched(self) -> int:
        return sum(d.exact_matched for d in self.days)


class BacklogService:
    def __init__(
        self,
        session_factory: SessionFactory,
        settings: RunSettings,
        clock: Callable[[], datetime],
        parallel_map: ParallelMap,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._clock = clock
        self._map = parallel_map

    def run(
        self,
        *,
        batch_key: str,
        banks: list[str],
        requested_by: str,
        date_from: date | None = None,
        date_to: date | None = None,
        queue_full: bool = True,
    ) -> list[BankBacklogResult]:
        def one_bank(bank_code: str) -> BankBacklogResult:
            return self._process_bank(
                bank_code,
                batch_key=batch_key,
                requested_by=requested_by,
                date_from=date_from,
                date_to=date_to,
                queue_full=queue_full,
            )

        return list(self._map(one_bank, banks))

    def run_queued(self, run_id: uuid.UUID) -> None:
        """Starts a PENDING run created by a backlog pass (the background half)."""
        with self._session_factory() as session:
            service = RunService(session, self._settings, self._clock)
            try:
                service.activate(run_id)
                session.commit()
            except RunAlreadyActiveError:
                run = session.get(ReconciliationRun, run_id)
                if run is not None and run.status == "PENDING":
                    run.status = "FAILED"
                    run.error_summary = "another run for this bank was active; queue it again"
                    run.completed_at = self._clock()
                    session.commit()
                return
            service.execute(run_id)
            session.commit()

    def _process_bank(
        self,
        bank_code: str,
        *,
        batch_key: str,
        requested_by: str,
        date_from: date | None,
        date_to: date | None,
        queue_full: bool,
    ) -> BankBacklogResult:
        result = BankBacklogResult(bank_code)
        with self._session_factory() as session:
            service = RunService(session, self._settings, self._clock)
            for day in self._days_newest_first(session, bank_code, date_from, date_to):
                key = f"backlog:{batch_key}:{bank_code}:{day.isoformat()}"
                try:
                    started = service.start(
                        bank_code=bank_code,
                        idempotency_key=key,
                        requested_by=requested_by,
                        mode="FAST",
                        date_from=day,
                        date_to=day,
                    )
                    session.commit()
                    if started.created:
                        service.execute(started.run.id)
                        session.commit()
                except RunAlreadyActiveError:
                    session.rollback()
                    result.failed_days.append(day)
                    continue
                run = session.get(ReconciliationRun, started.run.id)
                assert run is not None
                if run.status != "COMPLETED":
                    result.failed_days.append(day)
                matched = int((run.metrics or {}).get("exact_matched", 0))
                result.days.append(DayRun(day, run.id, run.status, matched))

            if queue_full:
                queued = service.start(
                    bank_code=bank_code,
                    idempotency_key=f"backlog:{batch_key}:{bank_code}:full",
                    requested_by=requested_by,
                    mode="FULL",
                    date_from=date_from,
                    date_to=date_to,
                    defer=True,
                )
                session.commit()
                if queued.run.status == "PENDING":
                    result.queued_full_run_id = queued.run.id
        return result

    @staticmethod
    def _days_newest_first(
        session: Session, bank_code: str, date_from: date | None, date_to: date | None
    ) -> list[date]:
        """Days that still have unmatched, unclaimed transactions, most recent first."""
        nt = NormalisedTransaction
        query = (
            select(nt.txn_date)
            .where(
                nt.bank_code == bank_code,
                nt.match_status == "UNMATCHED",
                ~select(MatchClaim.id)
                .where(MatchClaim.normalised_transaction_id == nt.id, MatchClaim.status == "ACTIVE")
                .exists(),
            )
            .distinct()
            .order_by(nt.txn_date.desc())
        )
        if date_from is not None:
            query = query.where(nt.txn_date >= date_from)
        if date_to is not None:
            query = query.where(nt.txn_date <= date_to)
        return list(session.scalars(query))
