# tests/integration/test_runs_service.py
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.persistence.models import MatchResult, ReconciliationRun
from recon.runs.service import (
    IdempotencyConflictError,
    RunAlreadyActiveError,
    RunService,
    RunStateError,
)
from recon.runs.settings import default_settings
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _service(session: Session, now: datetime = _NOW) -> RunService:
    return RunService(session, default_settings(), lambda: now)


def _start(service: RunService, bank: str = "HDFC", key: str | None = None) -> Any:
    return service.start(
        bank_code=bank, idempotency_key=key or uuid.uuid4().hex, requested_by="tester"
    )


def test_start_creates_a_running_run(db_session: Session) -> None:
    started = _start(_service(db_session))
    assert started.created is True
    assert (started.run.status, started.run.bank_code, started.run.started_at) == (
        "RUNNING",
        "HDFC",
        _NOW,
    )


def test_same_key_and_bank_replays_the_original_run(db_session: Session) -> None:
    service = _service(db_session)
    first = _start(service, key="key-replay-1")
    second = _start(service, key="key-replay-1")
    assert (second.created, second.run.id) == (False, first.run.id)
    assert db_session.query(ReconciliationRun).count() == 1


def test_same_key_with_a_different_bank_is_a_conflict(db_session: Session) -> None:
    service = _service(db_session)
    _start(service, bank="HDFC", key="key-conflict-1")
    with pytest.raises(IdempotencyConflictError):
        _start(service, bank="ICICI", key="key-conflict-1")


def test_a_concurrent_request_with_the_same_key_resolves_to_the_winner(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates the race: the first lookup misses, then the insert collides
    with the winner's row, and the loser must return that row."""
    service = _service(db_session)
    first = _start(service, key="key-race-0001")
    real = service._by_key
    calls: list[str] = []

    def flaky(key: str) -> ReconciliationRun | None:
        calls.append(key)
        return None if len(calls) == 1 else real(key)

    monkeypatch.setattr(service, "_by_key", flaky)

    second = _start(service, key="key-race-0001")

    assert (second.created, second.run.id) == (False, first.run.id)


def test_a_second_run_for_a_busy_bank_is_rejected_but_another_bank_is_fine(
    db_session: Session,
) -> None:
    service = _service(db_session)
    _start(service, bank="HDFC")
    with pytest.raises(RunAlreadyActiveError):
        _start(service, bank="HDFC")
    assert _start(service, bank="ICICI").created is True


def test_a_finished_run_does_not_block_the_next_one(db_session: Session) -> None:
    service = _service(db_session)
    first = _start(service)
    db_session.commit()
    service.execute(first.run.id)
    assert _start(service).created is True


def test_a_stale_running_run_is_reaped_only_past_the_window(db_session: Session) -> None:
    old = _start(_service(db_session))
    with pytest.raises(RunAlreadyActiveError):
        _start(_service(db_session, _NOW + timedelta(minutes=29)))

    fresh = _start(_service(db_session, _NOW + timedelta(minutes=31)))

    db_session.refresh(old.run)
    assert fresh.created is True
    assert old.run.status == "FAILED"
    assert "reaped" in (old.run.error_summary or "")


def test_execute_matches_classifies_and_records_metrics(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all([make_txn(f.id, source="INTERNAL"), make_txn(f.id, source="EXTERNAL")])
    db_session.flush()
    service = _service(db_session)
    started = _start(service)
    db_session.commit()

    service.execute(started.run.id)

    run = db_session.get(ReconciliationRun, started.run.id)
    assert run is not None
    assert (run.status, run.completed_at) == ("COMPLETED", _NOW)
    assert run.metrics is not None
    assert run.metrics["exact_matched"] == 1
    assert run.metrics["exceptions_created"] == 0
    result = db_session.scalars(select(MatchResult)).one()
    assert result.run_id == str(run.id)


def test_a_failed_run_rolls_back_its_matching_work_and_hides_the_detail(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Boom:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def classify_unmatched(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("secret detail")

    f = make_ingestion_file(db_session)
    internal = make_txn(f.id, source="INTERNAL")
    db_session.add_all([internal, make_txn(f.id, source="EXTERNAL")])
    db_session.flush()
    service = _service(db_session)
    started = _start(service)
    db_session.commit()
    monkeypatch.setattr("recon.runs.service.ExceptionClassifier", Boom)

    service.execute(started.run.id)

    run = db_session.get(ReconciliationRun, started.run.id)
    assert run is not None
    assert (run.status, run.error_summary) == ("FAILED", "RuntimeError")
    assert "secret" not in str(run.error_summary)
    assert (
        db_session.query(MatchResult).count() == 0
    )  # the matching done before the failure is gone
    db_session.refresh(internal)
    assert internal.match_status == "UNMATCHED"


def test_executing_a_finished_run_is_a_state_error(db_session: Session) -> None:
    service = _service(db_session)
    started = _start(service)
    db_session.commit()
    service.execute(started.run.id)
    with pytest.raises(RunStateError):
        service.execute(started.run.id)
