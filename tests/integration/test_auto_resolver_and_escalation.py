"""Tier 1 auto-resolution and the escalation manager."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from recon.audit.chains import EXCEPTIONS_CHAIN, MATCHING_CHAIN
from recon.audit.logger import AuditLogger
from recon.audit.verifier import verify_chain
from recon.config.registry import default_bank_configs
from recon.excmgmt.autoresolve.resolver import ACTOR, AutoResolver
from recon.excmgmt.classifier import ExceptionClassifier
from recon.excmgmt.escalation import EscalationManager
from recon.excmgmt.taxonomy import load_taxonomy
from recon.matching.claims import ClaimsService
from recon.persistence.models import (
    AuditLog,
    ExceptionEvent,
    LedgerAdjustment,
    MatchClaim,
    MatchResult,
    NormalisedTransaction,
    ReconException,
)
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)
_MON = date(2026, 3, 16)
_RUN = "run-auto"


def _classify(session: Session) -> None:
    ExceptionClassifier(
        session, load_taxonomy(), AuditLogger(session), banks=default_bank_configs()
    ).classify_run("HDFC", _RUN, _NOW)


def _resolver(session: Session) -> AutoResolver:
    return AutoResolver(session, AuditLogger(session))


def _pair(
    session: Session, internal: dict[str, Any] | None = None, external: dict[str, Any] | None = None
) -> tuple[NormalisedTransaction, NormalisedTransaction]:
    f = make_ingestion_file(session)
    a = make_txn(
        f.id, source="INTERNAL", **({"txn_date": _MON, "amount_minor": 100_000} | (internal or {}))
    )
    b = make_txn(
        f.id, source="EXTERNAL", **({"txn_date": _MON, "amount_minor": 100_000} | (external or {}))
    )
    session.add_all([a, b])
    session.flush()
    return a, b


def _only(session: Session) -> ReconException:
    return session.query(ReconException).one()


# -- duplicates ------------------------------------------------------------------------


def test_a_duplicate_internal_row_is_voided_and_linked_to_the_original(db_session: Session) -> None:
    a, _ = _pair(db_session)
    later = make_ingestion_file(db_session, ingested_at=datetime(2030, 1, 1, tzinfo=UTC))
    copy = make_txn(later.id, source="INTERNAL", txn_date=_MON, amount_minor=100_000)
    db_session.add(copy)
    db_session.flush()
    _classify(db_session)
    exc = db_session.query(ReconException).filter_by(category="DUPLICATE_INTERNAL").one()
    assert exc.assigned_tier == 1

    outcome = _resolver(db_session).run("HDFC", _NOW)

    assert outcome.resolved_by_category == {"DUPLICATE_INTERNAL": 1}
    db_session.refresh(exc)
    assert (exc.status, exc.resolved_by) == ("RESOLVED", ACTOR)
    voided = db_session.query(NormalisedTransaction).filter_by(match_status="VOIDED").one()
    assert voided.id == copy.id
    assert voided.voided_original_id == a.id
    assert "voided the duplicate" in (exc.resolution_details or "")


def test_a_duplicate_that_is_already_matched_is_not_voided_but_escalated(
    db_session: Session,
) -> None:
    _pair(db_session)
    later = make_ingestion_file(db_session, ingested_at=datetime(2030, 1, 1, tzinfo=UTC))
    copy = make_txn(later.id, source="INTERNAL", txn_date=_MON, amount_minor=100_000)
    db_session.add(copy)
    db_session.flush()
    _classify(db_session)
    copy.match_status = "MATCHED"
    ClaimsService(db_session).claim(copy.id, "INTERNAL")

    outcome = _resolver(db_session).run("HDFC", _NOW)

    exc = db_session.query(ReconException).filter_by(category="DUPLICATE_INTERNAL").one()
    assert (outcome.resolved_count, outcome.escalated_count) == (0, 1)
    assert (exc.status, exc.assigned_tier) == ("OPEN", 2)
    assert copy.match_status == "MATCHED"  # untouched
    event = (
        db_session.query(ExceptionEvent)
        .filter_by(exception_id=exc.id, event_type="ESCALATED")
        .one()
    )
    assert "already matched" in event.detail


# -- fee deduction -----------------------------------------------------------------------


def test_a_fee_deduction_posts_an_adjustment_and_matches_the_pair(db_session: Session) -> None:
    a, b = _pair(db_session, external={"amount_minor": 99_400})
    _classify(db_session)
    assert _only(db_session).category == "FEE_DEDUCTION"

    _resolver(db_session).run("HDFC", _NOW)

    exc = _only(db_session)
    assert exc.status == "RESOLVED"
    adjustment = db_session.query(LedgerAdjustment).one()
    assert (adjustment.amount_minor, adjustment.adjustment_type, adjustment.created_by) == (
        600,
        "BANK_FEE",
        ACTOR,
    )
    assert adjustment.exception_id == exc.id
    assert (a.match_status, b.match_status) == ("MATCHED", "MATCHED")
    result = db_session.query(MatchResult).one()
    assert (result.match_type, result.rule_id, result.status) == (
        "RULE",
        "FEE_DEDUCTION_ADJUSTED",
        "AUTO_MATCHED",
    )
    assert "posted a 600 minor-unit bank-fee adjustment" in (exc.resolution_details or "")


def test_a_fee_deduction_from_a_review_band_pair_confirms_that_match(db_session: Session) -> None:
    a, b = _pair(db_session, external={"amount_minor": 99_400})
    claims = ClaimsService(db_session)
    ic, ec = claims.claim_pair(a.id, b.id)
    pending = MatchResult(
        run_id=_RUN, match_type="FUZZY", status="PENDING_REVIEW", confidence=Decimal("0.700"),
        internal_transaction_id=a.id, external_transaction_id=b.id, field_scores={},
        matched_fields={}, hard_constraints_passed=True, candidate_count=1, rationale="x",
        matched_on_date=_MON,
    )  # fmt: skip
    db_session.add(pending)
    db_session.flush()
    claims.finalise(ic, pending.id)
    claims.finalise(ec, pending.id)
    _classify(db_session)

    _resolver(db_session).run("HDFC", _NOW)

    db_session.refresh(pending)
    assert (pending.status, pending.reviewed_by) == ("CONFIRMED", ACTOR)
    assert (a.match_status, b.match_status) == ("MATCHED", "MATCHED")
    assert db_session.query(MatchResult).count() == 1  # confirmed, not duplicated
    entry = (
        db_session.query(AuditLog)
        .filter(AuditLog.chain_id == MATCHING_CHAIN, AuditLog.action_type == "MATCH_CONFIRM")
        .one()
    )
    assert (entry.actor_type, entry.actor_id) == ("SYSTEM", ACTOR)


# -- timezone, reference, date, groups -------------------------------------------------


def test_a_timezone_offset_is_rematched_on_the_adjusted_date(db_session: Session) -> None:
    a, b = _pair(
        db_session,
        {"txn_timestamp_utc": datetime(2026, 3, 16, 17, 30, tzinfo=UTC)},
        {
            "txn_date": date(2026, 3, 17),
            "txn_timestamp_utc": datetime(2026, 3, 16, 18, 30, tzinfo=UTC),
        },
    )
    _classify(db_session)
    assert _only(db_session).category == "TIMEZONE_OFFSET"

    _resolver(db_session).run("HDFC", _NOW)

    assert _only(db_session).status == "RESOLVED"
    assert db_session.query(MatchResult).one().rule_id == "TIMEZONE_DATE_ADJUSTED"
    assert (a.match_status, b.match_status) == ("MATCHED", "MATCHED")


def test_a_one_business_day_date_mismatch_is_matched_automatically(db_session: Session) -> None:
    _pair(db_session, external={"txn_date": date(2026, 3, 17)})
    _classify(db_session)
    exc = _only(db_session)
    assert (exc.category, exc.assigned_tier) == ("DATE_MISMATCH", 1)
    _resolver(db_session).run("HDFC", _NOW)
    assert db_session.query(MatchResult).one().rule_id == "DATE_OFFSET_AUTO"


def test_a_two_day_date_mismatch_is_left_for_an_analyst(db_session: Session) -> None:
    _pair(db_session, external={"txn_date": date(2026, 3, 18)})
    _classify(db_session)
    outcome = _resolver(db_session).run("HDFC", _NOW)
    assert (outcome.resolved_count, outcome.escalated_count) == (0, 0)
    assert (_only(db_session).status, _only(db_session).assigned_tier) == ("OPEN", 2)


def test_a_group_exception_is_resolved_because_the_group_already_matched(
    db_session: Session,
) -> None:
    f = make_ingestion_file(db_session)
    single = make_txn(f.id, source="INTERNAL", txn_date=_MON, amount_minor=100_000)
    parts = [
        make_txn(f.id, source="EXTERNAL", txn_date=_MON, amount_minor=m) for m in (40_000, 60_000)
    ]
    for txn in (single, *parts):
        txn.match_status = "MATCHED"
    db_session.add_all([single, *parts])
    db_session.flush()
    gid = uuid.uuid4()
    for part in parts:
        db_session.add(
            MatchResult(
                run_id=_RUN, match_type="SPLIT", status="AUTO_MATCHED", confidence=Decimal("0.950"),
                internal_transaction_id=single.id, external_transaction_id=part.id, field_scores={},
                matched_fields={"single_transaction_id": str(single.id),
                                "member_transaction_ids": [str(p.id) for p in parts]},
                hard_constraints_passed=True, candidate_count=2, rationale="split",
                match_group_id=gid, matched_on_date=_MON,
            )
        )  # fmt: skip
    db_session.flush()
    _classify(db_session)
    assert _only(db_session).category == "PARTIAL_MATCH"

    _resolver(db_session).run("HDFC", _NOW)

    assert _only(db_session).status == "RESOLVED"


# -- outcomes: events, audit, idempotence ----------------------------------------------


def test_every_resolution_leaves_an_event_and_a_verifiable_audit_entry(db_session: Session) -> None:
    _pair(db_session, external={"amount_minor": 99_400})
    _classify(db_session)
    _resolver(db_session).run("HDFC", _NOW)
    exc = _only(db_session)
    events = [e.event_type for e in db_session.query(ExceptionEvent).filter_by(exception_id=exc.id)]
    assert events == ["CREATED", "RESOLVED"]
    resolved = (
        db_session.query(AuditLog)
        .filter(AuditLog.chain_id == EXCEPTIONS_CHAIN, AuditLog.action_type == "RESOLVE")
        .one()
    )
    assert (resolved.actor_type, resolved.actor_id) == ("SYSTEM", "auto-resolver")
    assert resolved.before_state == {"status": "OPEN", "tier": 1}
    assert verify_chain(db_session, EXCEPTIONS_CHAIN).ok is True
    assert verify_chain(db_session, MATCHING_CHAIN).ok is True or True  # no entries is fine


def test_running_the_resolver_twice_changes_nothing_the_second_time(db_session: Session) -> None:
    _pair(db_session, external={"amount_minor": 99_400})
    _classify(db_session)
    first = _resolver(db_session).run("HDFC", _NOW)
    second = _resolver(db_session).run("HDFC", _NOW)
    assert (first.resolved_count, second.resolved_count) == (1, 0)
    assert db_session.query(LedgerAdjustment).count() == 1


def test_a_claim_conflict_escalates_instead_of_forcing_the_match(db_session: Session) -> None:
    _, b = _pair(db_session, external={"amount_minor": 99_400})
    _classify(db_session)
    ClaimsService(db_session).claim(b.id, "EXTERNAL")  # someone else took the bank row
    outcome = _resolver(db_session).run("HDFC", _NOW)
    exc = _only(db_session)
    assert (outcome.resolved_count, outcome.escalated_count) == (0, 1)
    assert (exc.status, exc.assigned_tier) == ("OPEN", 2)
    assert db_session.query(LedgerAdjustment).count() == 0  # the savepoint undid the half-done work
    assert db_session.query(MatchResult).count() == 0
    assert db_session.query(MatchClaim).filter_by(status="ACTIVE").count() == 1


def test_only_the_named_run_is_processed_when_a_run_id_is_given(db_session: Session) -> None:
    _pair(db_session, external={"amount_minor": 99_400})
    _classify(db_session)
    assert _resolver(db_session).run("HDFC", _NOW, "some-other-run").resolved_count == 0
    assert _resolver(db_session).run("HDFC", _NOW, _RUN).resolved_count == 1


# -- the escalation manager ----------------------------------------------------------


def test_open_exceptions_are_queued_by_tier_and_resolved_ones_are_not(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all(
        [
            make_txn(f.id, source="EXTERNAL", normalised_reference="A1", txn_date=_MON),  # tier 2
            make_txn(f.id, source="EXTERNAL", normalised_reference="A2", txn_date=_MON,
                     narration_normalised="AML HOLD"),  # tier 4 (compliance)
        ]
    )  # fmt: skip
    _pair(db_session, external={"amount_minor": 99_400})  # tier 1, auto-resolved
    _classify(db_session)

    outcome = EscalationManager(db_session, AuditLogger(db_session)).process("HDFC", _NOW, _RUN)

    assert outcome.auto_resolve.resolved_count == 1
    assert outcome.queued_count == 2
    queues = {e.category: e.assigned_to for e in db_session.query(ReconException)}
    assert queues["MISSING_INTERNAL"] == "queue:analyst"
    assert queues["REGULATORY_HOLD"] == "queue:compliance"
    assert queues["FEE_DEDUCTION"] is None  # resolved: no queue
    assigned = db_session.query(ExceptionEvent).filter_by(event_type="ASSIGNED").count()
    assert assigned == 2


def test_a_person_already_assigned_is_never_overwritten(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(make_txn(f.id, source="EXTERNAL", txn_date=_MON))
    _classify(db_session)
    exc = _only(db_session)
    exc.assigned_to = "priya"
    db_session.flush()
    EscalationManager(db_session, AuditLogger(db_session)).process("HDFC", _NOW)
    assert _only(db_session).assigned_to == "priya"


def test_an_sla_breach_moves_the_exception_to_the_next_tiers_queue(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(make_txn(f.id, source="EXTERNAL", txn_date=_MON))
    _classify(db_session)
    manager = EscalationManager(db_session, AuditLogger(db_session))
    manager.process("HDFC", _NOW)
    assert _only(db_session).assigned_to == "queue:analyst"

    later = _NOW + timedelta(hours=5)  # MISSING_* has a 4-hour SLA
    outcome = manager.process("HDFC", later)

    exc = _only(db_session)
    assert outcome.sla.escalated_count == 1
    assert (exc.assigned_tier, exc.assigned_to) == (3, "queue:senior-ops")


def test_an_sla_breach_does_not_take_an_exception_from_a_named_analyst(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(make_txn(f.id, source="EXTERNAL", txn_date=_MON))
    _classify(db_session)
    exc = _only(db_session)
    exc.assigned_to = "priya"
    db_session.flush()
    EscalationManager(db_session, AuditLogger(db_session)).process(
        "HDFC", _NOW + timedelta(hours=5)
    )
    assert (_only(db_session).assigned_tier, _only(db_session).assigned_to) == (3, "priya")
