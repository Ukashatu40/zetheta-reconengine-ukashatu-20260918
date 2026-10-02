# tests/integration/test_exception_audit.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.audit.chains import EXCEPTIONS_CHAIN
from recon.audit.logger import AuditLogger
from recon.audit.verifier import verify_chain
from recon.excmgmt.classifier import ExceptionClassifier
from recon.excmgmt.sla import SlaScanner
from recon.excmgmt.taxonomy import load_taxonomy
from recon.persistence.models import AuditLog
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _actions(session: Session) -> list[str]:
    return list(
        session.scalars(
            select(AuditLog.action_type)
            .where(AuditLog.chain_id == EXCEPTIONS_CHAIN)
            .order_by(AuditLog.sequence_no)
        )
    )


def _classify(session: Session) -> ExceptionClassifier:
    f = make_ingestion_file(session)
    session.add_all(
        [make_txn(f.id, source="EXTERNAL", normalised_reference="A"),
         make_txn(f.id, source="INTERNAL", normalised_reference="B")]
    )  # fmt: skip
    session.flush()
    classifier = ExceptionClassifier(session, load_taxonomy(), AuditLogger(session))
    classifier.classify_unmatched("HDFC", "run-1", _NOW)
    return classifier


def test_each_created_exception_is_audited_and_the_chain_verifies(db_session: Session) -> None:
    _classify(db_session)

    assert _actions(db_session) == ["EXCEPTION_CREATE", "EXCEPTION_CREATE"]
    assert verify_chain(db_session, EXCEPTIONS_CHAIN).ok is True


def test_reclassifying_adds_no_audit_entries(db_session: Session) -> None:
    classifier = _classify(db_session)

    classifier.classify_unmatched("HDFC", "run-2", _NOW)

    assert len(_actions(db_session)) == 2


def test_sla_escalation_is_audited_on_the_same_chain(db_session: Session) -> None:
    _classify(db_session)

    SlaScanner(db_session, AuditLogger(db_session)).scan(_NOW + timedelta(minutes=241))

    assert _actions(db_session) == ["EXCEPTION_CREATE", "EXCEPTION_CREATE", "ESCALATE", "ESCALATE"]
    assert verify_chain(db_session, EXCEPTIONS_CHAIN).ok is True
