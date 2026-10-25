"""The classifier's new paths: review-band pairs, matched pairs, groups, and the
single-record categories. Together with the older tests this reaches all 19
categories (see test_every_category_is_reachable)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from recon.audit.logger import AuditLogger
from recon.config.registry import default_bank_configs
from recon.domain.enums import ExceptionCategory
from recon.excmgmt.classifier import ExceptionClassifier
from recon.excmgmt.taxonomy import load_taxonomy
from recon.fx.service import FxService
from recon.matching.claims import ClaimsService
from recon.persistence.models import (
    FxRate,
    MatchResult,
    NormalisedTransaction,
    RawTransaction,
    ReconException,
)
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)  # a Monday
_MON = date(2026, 3, 16)
_RUN = "run-cat"


def _classifier(session: Session) -> ExceptionClassifier:
    return ExceptionClassifier(
        session, load_taxonomy(), AuditLogger(session), banks=default_bank_configs()
    )


def _pair(
    session: Session, internal: dict[str, Any] | None = None, external: dict[str, Any] | None = None
) -> tuple[NormalisedTransaction, NormalisedTransaction]:
    f = make_ingestion_file(session)
    a = make_txn(f.id, source="INTERNAL", **({"txn_date": _MON} | (internal or {})))
    b = make_txn(f.id, source="EXTERNAL", **({"txn_date": _MON} | (external or {})))
    session.add_all([a, b])
    session.flush()
    return a, b


def _result(
    session: Session,
    internal: NormalisedTransaction,
    external: NormalisedTransaction,
    status: str,
    *,
    confidence: str = "0.700",
    **extra: Any,
) -> MatchResult:
    values: dict[str, Any] = {
        "run_id": _RUN,
        "match_type": "FUZZY",
        "status": status,
        "confidence": Decimal(confidence),
        "internal_transaction_id": internal.id,
        "external_transaction_id": external.id,
        "field_scores": {},
        "matched_fields": {},
        "hard_constraints_passed": True,
        "candidate_count": 1,
        "rationale": "test",
        "matched_on_date": external.txn_date,
    } | extra
    result = MatchResult(**values)
    session.add(result)
    session.flush()
    return result


def _categories(session: Session) -> list[str]:
    return sorted(e.category for e in session.query(ReconException))


# -- leftover pairs ----------------------------------------------------------------


def test_a_leftover_pair_with_a_fee_shaped_gap_is_a_fee_deduction(db_session: Session) -> None:
    _pair(db_session, {"amount_minor": 100_000}, {"amount_minor": 99_400})  # HDFC fee: 500 + 0.10%
    _classifier(db_session).classify_unmatched("HDFC", _RUN, _NOW)
    exc = db_session.query(ReconException).one()
    assert (exc.category, exc.assigned_tier) == ("FEE_DEDUCTION", 1)  # ALWAYS auto-resolvable


def test_leftovers_are_paired_by_identical_reference_only(db_session: Session) -> None:
    """A cut reference is found by the fuzzy matcher (and then named by
    classify_pending_review / classify_matched), not by pairing leftovers."""
    long_ref = "ABCDEFGHIJKLMNOPQRST"
    a, b = _pair(
        db_session,
        {"normalised_reference": long_ref},
        {"normalised_reference": long_ref[:16]},
    )
    outcome = _classifier(db_session).classify_unmatched("HDFC", _RUN, _NOW)
    # the references differ, so the leftover path pairs by reference and sees two MISSING
    assert outcome.created_by_category == {"MISSING_INTERNAL": 1, "MISSING_EXTERNAL": 1}
    assert (a.match_status, b.match_status) == ("UNMATCHED", "UNMATCHED")


def test_regulatory_hold_on_a_lone_record_outranks_missing(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(make_txn(f.id, source="EXTERNAL", narration_normalised="HELD FOR AML REVIEW"))
    db_session.flush()
    _classifier(db_session).classify_unmatched("HDFC", _RUN, _NOW)
    exc = db_session.query(ReconException).one()
    assert (exc.category, exc.assigned_tier, exc.severity) == ("REGULATORY_HOLD", 4, "CRITICAL")
    assert exc.sla_deadline == _NOW + timedelta(minutes=2880)


def test_an_internal_reversal_with_no_bank_entry_is_reversal_pending(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(make_txn(f.id, source="INTERNAL", is_reversal=True))
    db_session.flush()
    _classifier(db_session).classify_unmatched("HDFC", _RUN, _NOW)
    assert _categories(db_session) == ["REVERSAL_PENDING"]


def test_a_reversal_narration_also_counts(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(make_txn(f.id, source="INTERNAL", narration_normalised="REVERSAL OF REF9"))
    db_session.flush()
    _classifier(db_session).classify_unmatched("HDFC", _RUN, _NOW)
    assert _categories(db_session) == ["REVERSAL_PENDING"]


def test_an_external_reversal_is_not_reversal_pending(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(make_txn(f.id, source="EXTERNAL", is_reversal=True))
    db_session.flush()
    _classifier(db_session).classify_unmatched("HDFC", _RUN, _NOW)
    assert _categories(db_session) == ["MISSING_INTERNAL"]


def test_an_internal_entry_older_than_the_cycle_but_inside_the_window_is_a_settlement_delay(
    db_session: Session,
) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(make_txn(f.id, source="INTERNAL", txn_date=date(2026, 3, 12)))  # Thursday
    db_session.flush()
    # HDFC: T+1 cycle. Window widened here so the entry is late but not yet stale.
    _classifier(db_session).classify_unmatched(
        "HDFC", _RUN, _NOW, as_of_date=_MON, stale_after_days=10
    )
    assert _categories(db_session) == ["SETTLEMENT_DELAY"]


def test_an_entry_past_the_window_is_stale_not_delayed(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(make_txn(f.id, source="INTERNAL", txn_date=date(2026, 3, 2)))
    db_session.flush()
    _classifier(db_session).classify_unmatched(
        "HDFC", _RUN, _NOW, as_of_date=_MON, stale_after_days=5
    )
    assert _categories(db_session) == ["STALE_TRANSACTION"]


# -- review-band pairs ---------------------------------------------------------------


def test_a_review_band_pair_with_a_fee_shaped_gap_is_named_and_linked_to_its_match(
    db_session: Session,
) -> None:
    a, b = _pair(db_session, {"amount_minor": 100_000}, {"amount_minor": 99_400})
    result = _result(db_session, a, b, "PENDING_REVIEW")
    ClaimsService(db_session).claim_pair(a.id, b.id)

    outcome = _classifier(db_session).classify_pending_review("HDFC", _RUN, _NOW)

    assert outcome.created_by_category == {"FEE_DEDUCTION": 1}
    exc = db_session.query(ReconException).one()
    assert exc.affected_records["match_result_id"] == str(result.id)
    assert exc.assigned_tier == 1  # a fee that matches the schedule is Tier 1 (ALWAYS)


def test_a_review_band_pair_with_nothing_wrong_creates_no_exception(db_session: Session) -> None:
    a, b = _pair(db_session)
    _result(db_session, a, b, "PENDING_REVIEW")
    assert _classifier(db_session).classify_pending_review("HDFC", _RUN, _NOW).created_count == 0


def test_only_the_runs_own_review_pairs_are_classified(db_session: Session) -> None:
    a, b = _pair(db_session, {"amount_minor": 100_000}, {"amount_minor": 99_400})
    _result(db_session, a, b, "PENDING_REVIEW")
    assert (
        _classifier(db_session).classify_pending_review("HDFC", "another-run", _NOW).created_count
        == 0
    )


# -- matched pairs -----------------------------------------------------------------


def test_a_matched_pair_with_a_late_date_is_a_date_mismatch_and_stays_matched(
    db_session: Session,
) -> None:
    a, b = _pair(db_session)
    b.txn_date = date(2026, 3, 20)  # same ref and amount, four days later
    b.txn_timestamp_utc = datetime.combine(b.txn_date, time(12), tzinfo=UTC)
    db_session.flush()
    result = _result(db_session, a, b, "AUTO_MATCHED", confidence="1.000")
    a.match_status = b.match_status = "MATCHED"

    outcome = _classifier(db_session).classify_matched("HDFC", _RUN, _NOW)

    assert outcome.created_by_category["DATE_MISMATCH"] == 1
    exc = db_session.query(ReconException).filter_by(category="DATE_MISMATCH").one()
    assert exc.dedupe_key == f"DATE_MISMATCH:matched:{result.id}"
    assert exc.affected_records["already_matched"] is True
    assert (a.match_status, b.match_status) == ("MATCHED", "MATCHED")  # nothing is unmatched


def test_matched_pairs_that_agree_cost_no_exception(db_session: Session) -> None:
    a, b = _pair(db_session)
    _result(db_session, a, b, "AUTO_MATCHED", confidence="1.000")
    assert _classifier(db_session).classify_matched("HDFC", _RUN, _NOW).created_count == 0


def test_a_matched_pair_settled_late_is_a_settlement_delay(db_session: Session) -> None:
    a, b = _pair(db_session, {"txn_date": date(2026, 3, 11)}, {"txn_date": date(2026, 3, 11)})
    b.settlement_date = date(2026, 3, 16)  # Wed 11 -> Mon 16: 3 business days against T+1
    _result(db_session, a, b, "AUTO_MATCHED", confidence="1.000")
    outcome = _classifier(db_session).classify_matched("HDFC", _RUN, _NOW)
    assert outcome.created_by_category == {"SETTLEMENT_DELAY": 1}


def test_a_matched_pair_under_a_compliance_hold_is_a_regulatory_hold(db_session: Session) -> None:
    a, b = _pair(db_session, external={"narration_normalised": "FIU QUERY OPEN"})
    _result(db_session, a, b, "AUTO_MATCHED", confidence="1.000")
    outcome = _classifier(db_session).classify_matched("HDFC", _RUN, _NOW)
    assert outcome.created_by_category == {"REGULATORY_HOLD": 1}


def test_a_matched_pair_with_a_truncated_reference_is_recorded(db_session: Session) -> None:
    long_ref = "ABCDEFGHIJKLMNOPQRST"
    a, b = _pair(
        db_session, {"normalised_reference": long_ref}, {"normalised_reference": long_ref[:16]}
    )
    _result(db_session, a, b, "AUTO_MATCHED", confidence="0.920")
    outcome = _classifier(db_session).classify_matched("HDFC", _RUN, _NOW)
    assert outcome.created_by_category == {"REFERENCE_TRUNCATED": 1}
    exc = db_session.query(ReconException).one()
    assert exc.assigned_tier == 1  # 0.92 is above the 0.85 auto-resolve confidence


def test_classifying_matched_pairs_is_idempotent(db_session: Session) -> None:
    a, b = _pair(db_session)
    b.txn_date = date(2026, 3, 20)
    _result(db_session, a, b, "AUTO_MATCHED", confidence="1.000")
    classifier = _classifier(db_session)
    first = classifier.classify_matched("HDFC", _RUN, _NOW)
    second = classifier.classify_matched("HDFC", _RUN, _NOW)
    # late booking and late settlement are two findings on the same match
    assert (first.created_count, second.created_count, second.skipped_existing_count) == (2, 0, 2)


# -- groups ------------------------------------------------------------------------


def _group(session: Session, match_type: str) -> uuid.UUID:
    f = make_ingestion_file(session)
    single_source, member_source = (
        ("INTERNAL", "EXTERNAL") if match_type == "SPLIT" else ("EXTERNAL", "INTERNAL")
    )
    single = make_txn(f.id, source=single_source, txn_date=_MON, amount_minor=100_000)
    members = [
        make_txn(f.id, source=member_source, txn_date=_MON, amount_minor=40_000),
        make_txn(f.id, source=member_source, txn_date=_MON, amount_minor=60_000),
    ]
    session.add_all([single, *members])
    session.flush()
    group_id = uuid.uuid4()
    for member in members:
        internal, external = (single, member) if match_type == "SPLIT" else (member, single)
        _result(
            session,
            internal,
            external,
            "AUTO_MATCHED",
            confidence="0.950",
            match_type=match_type,
            match_group_id=group_id,
            matched_fields={
                "single_transaction_id": str(single.id),
                "member_transaction_ids": [str(m.id) for m in members],
            },
        )
    return group_id


@pytest.mark.parametrize(
    ("match_type", "category"),
    [("SPLIT", "PARTIAL_MATCH"), ("NETTED", "NETTED_SETTLEMENT")],
)
def test_each_group_becomes_one_exception_in_tier_1(
    db_session: Session, match_type: str, category: str
) -> None:
    group_id = _group(db_session, match_type)
    outcome = _classifier(db_session).classify_groups("HDFC", _RUN, _NOW)
    assert outcome.created_by_category == {category: 1}  # two member rows, one exception
    exc = db_session.query(ReconException).one()
    assert exc.dedupe_key == f"{category}:group:{group_id}"
    assert exc.assigned_tier == 1
    assert exc.affected_records["already_matched"] is True


# -- the cumulative daily value ----------------------------------------------------------


def test_a_days_exceptions_go_to_tier_3_when_their_total_passes_a_crore(
    db_session: Session,
) -> None:
    f = make_ingestion_file(db_session)
    # Each is below the INR 10 lakh individual threshold; together they pass INR 1 crore.
    for i in range(12):
        db_session.add(
            make_txn(
                f.id,
                source="EXTERNAL",
                txn_date=_MON,
                normalised_reference=f"BIG{i}",
                amount_minor=90_000_000,  # INR 9 lakh each: 12 x 9 lakh = INR 1.08 crore
                amount=Decimal("900000.00"),
            )
        )
    db_session.flush()
    _classifier(db_session).classify_unmatched("HDFC", _RUN, _NOW)
    assert {e.assigned_tier for e in db_session.query(ReconException)} == {3}


def test_a_days_exceptions_stay_in_tier_2_below_the_cumulative_threshold(
    db_session: Session,
) -> None:
    f = make_ingestion_file(db_session)
    for i in range(3):
        db_session.add(
            make_txn(f.id, source="EXTERNAL", normalised_reference=f"SMALL{i}", txn_date=_MON)
        )
    db_session.flush()
    _classifier(db_session).classify_unmatched("HDFC", _RUN, _NOW)
    assert {e.assigned_tier for e in db_session.query(ReconException)} == {2}


# -- reachability ----------------------------------------------------------------------


def _add(
    session: Session, file_id: uuid.UUID, source: str, ref: str, **overrides: Any
) -> NormalisedTransaction:
    values: dict[str, Any] = {"txn_date": _MON, "normalised_reference": ref} | overrides
    txn = make_txn(file_id, source=source, **values)
    session.add(txn)
    return txn


def _plant_pairs_and_singles(session: Session, file_id: uuid.UUID) -> None:
    def add(source: str, ref: str, **overrides: Any) -> NormalisedTransaction:
        return _add(session, file_id, source, ref, **overrides)

    add("INTERNAL", "R-DIR", direction="CR")
    add("EXTERNAL", "R-DIR", direction="DR")
    add("INTERNAL", "R-CUR")
    add("EXTERNAL", "R-CUR", currency="USD")
    add("INTERNAL", "R-AMT", amount_minor=100_000)
    add("EXTERNAL", "R-AMT", amount_minor=100_777)
    add("INTERNAL", "R-FEE", amount_minor=100_000)
    add("EXTERNAL", "R-FEE", amount_minor=99_400)  # HDFC's fee is 500 + 0.10% = 600
    add("INTERNAL", "R-DATE")
    add("EXTERNAL", "R-DATE", txn_date=date(2026, 3, 18))
    add("INTERNAL", "R-TZ", txn_timestamp_utc=datetime(2026, 3, 16, 17, 30, tzinfo=UTC))
    add(
        "EXTERNAL",
        "R-TZ",
        txn_date=date(2026, 3, 17),
        txn_timestamp_utc=datetime(2026, 3, 16, 18, 30, tzinfo=UTC),
    )
    add("EXTERNAL", "R-MISSING-INT")
    add("INTERNAL", "R-MISSING-EXT")
    add("INTERNAL", "R-STALE", txn_date=date(2026, 3, 2))
    add("INTERNAL", "R-REV", is_reversal=True)
    add("EXTERNAL", "R-HOLD", narration_normalised="AML HOLD")
    add("INTERNAL", "R-DELAY", txn_date=date(2026, 3, 11))
    add("INTERNAL", "R-DUPI")
    add("INTERNAL", "R-DUPI")
    add("EXTERNAL", "R-DUPE")
    add("EXTERNAL", "R-DUPE")
    session.flush()


def test_every_category_is_reachable_through_the_classifier(db_session: Session) -> None:
    """One scenario per category, all through the public classifier methods. The
    19 categories are the PDF's 18 plus SETTLEMENT_DELAY."""
    seen: set[str] = set()
    f = make_ingestion_file(db_session)
    _plant_pairs_and_singles(db_session, f.id)
    db_session.add(
        RawTransaction(
            ingestion_file_id=f.id,
            ingested_on=_MON,
            bank_code="HDFC",
            format_type="CSV",
            source_line_no=7,
            raw_payload={},
            parse_status="QUARANTINED",
            parse_errors={"errors": [{"code": "INVALID_DATE", "field": "d", "detail": "x"}]},
        )
    )
    db_session.flush()
    classifier = _classifier(db_session)
    seen |= set(classifier.classify_ingestion_file(f.id, _NOW).created_by_category)

    # a review-band pair whose reference was cut by the bank, and both group kinds
    long_ref = "ABCDEFGHIJKLMNOPQRST"
    a, b = _pair(
        db_session, {"normalised_reference": long_ref}, {"normalised_reference": long_ref[:16]}
    )
    _result(db_session, a, b, "PENDING_REVIEW")
    _group(db_session, "SPLIT")
    _group(db_session, "NETTED")
    seen |= set(
        classifier.classify_run("HDFC", _RUN, _NOW, as_of_date=_MON, stale_after_days=5)
        .created_by_category
    )  # fmt: skip

    # FX variance needs an exchange rate, so it runs on another bank with an FxService
    db_session.add(FxRate(rate_date=_MON, currency="USD", rate_inr=Decimal("83.4"), source="RBI"))
    g = make_ingestion_file(db_session, bank_code="ICICI")
    fx_pair = {"bank_code": "ICICI", "txn_date": _MON, "normalised_reference": "FX1"}
    db_session.add_all(
        [
            make_txn(g.id, source="INTERNAL", **fx_pair),
            make_txn(g.id, source="EXTERNAL", currency="USD", **fx_pair),
        ]
    )
    db_session.flush()
    with_fx = ExceptionClassifier(
        db_session,
        load_taxonomy(),
        AuditLogger(db_session),
        banks=default_bank_configs(),
        fx=FxService(db_session),
    )
    seen |= set(with_fx.classify_unmatched("ICICI", _RUN, _NOW).created_by_category)

    assert seen == {c.value for c in ExceptionCategory}
