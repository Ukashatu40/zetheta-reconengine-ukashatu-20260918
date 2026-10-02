# tests/integration/test_sla_scanner.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from recon.excmgmt.classifier import ExceptionClassifier
from recon.excmgmt.sla import SlaScanner
from recon.excmgmt.taxonomy import load_taxonomy
from recon.persistence.models import ExceptionEvent, ReconException
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _seed(session: Session, **txn_kwargs: object) -> list[ReconException]:
    f = make_ingestion_file(session)
    session.add(make_txn(f.id, source="EXTERNAL", **txn_kwargs))
    session.flush()
    ExceptionClassifier(session, load_taxonomy()).classify_unmatched("HDFC", "run-1", _NOW)
    return session.query(ReconException).all()


def test_breach_escalates_one_tier_once(db_session: Session) -> None:
    exc = _seed(db_session)[0]  # MISSING_INTERNAL: tier 2, SLA 240 minutes
    scanner = SlaScanner(db_session)

    first = scanner.scan(_NOW + timedelta(minutes=241))
    second = scanner.scan(_NOW + timedelta(minutes=500))

    assert (first.escalated_count, second.escalated_count) == (1, 0)
    assert (exc.assigned_tier, exc.status) == (3, "ESCALATED")  # A4.2: unresolved after 4 hours
    assert exc.sla_breached_at is not None
    events = db_session.query(ExceptionEvent).filter(ExceptionEvent.exception_id == exc.id).all()
    escalated = [e for e in events if e.event_type == "ESCALATED"]
    assert [(e.from_tier, e.to_tier) for e in escalated] == [(2, 3)]


def test_exception_inside_or_exactly_at_its_deadline_is_untouched(db_session: Session) -> None:
    exc = _seed(db_session)[0]
    scanner = SlaScanner(db_session)

    assert scanner.scan(_NOW + timedelta(minutes=239)).escalated_count == 0
    assert scanner.scan(_NOW + timedelta(minutes=240)).escalated_count == 0  # strictly past only
    assert exc.assigned_tier == 2


def test_breach_at_tier_4_is_recorded_without_changing_the_tier(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all(
        [
            make_txn(f.id, source="INTERNAL", direction="CR"),
            make_txn(f.id, source="EXTERNAL", direction="DR"),
        ]
    )
    db_session.flush()
    ExceptionClassifier(db_session, load_taxonomy()).classify_unmatched("HDFC", "run-1", _NOW)
    exc = db_session.query(ReconException).one()  # DIRECTION_REVERSAL, tier 4, 30 minutes
    scanner = SlaScanner(db_session)

    first = scanner.scan(_NOW + timedelta(minutes=31))
    second = scanner.scan(_NOW + timedelta(minutes=90))

    assert (first.breached_at_top_tier_count, first.escalated_count) == (1, 0)
    assert second.breached_at_top_tier_count == 0
    assert exc.assigned_tier == 4
    types = {
        e.event_type
        for e in db_session.query(ExceptionEvent).filter(ExceptionEvent.exception_id == exc.id)
    }
    assert types == {"CREATED", "SLA_BREACHED"}


def test_resolved_exceptions_are_ignored(db_session: Session) -> None:
    exc = _seed(db_session)[0]
    exc.status = "RESOLVED"
    db_session.flush()

    assert SlaScanner(db_session).scan(_NOW + timedelta(days=2)).escalated_count == 0
