"""Split and netted matching (A3.3), including the B4.2 cross-currency netting
puzzle. AE-32: GBP 142,857.14 at the UK partner's 84.00 is INR 11,999,999.76, not
the INR 1.5 crore the PDF states, so both versions are exercised."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.audit.chains import MATCHING_CHAIN
from recon.audit.logger import AuditLogger
from recon.audit.verifier import verify_chain
from recon.fx.service import FxService
from recon.matching.claims import ClaimsService
from recon.matching.strategies.group import GroupMatchConfig, GroupMatchingStrategy
from recon.persistence.models import (
    AuditLog,
    FxRate,
    MatchClaim,
    MatchResult,
    NormalisedTransaction,
)
from tests.integration.factories import make_ingestion_file, make_txn

_DAY = date(2026, 3, 15)
_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _txn(
    session: Session, source: str, ref: str, minor: int, **extra: Any
) -> NormalisedTransaction:
    f = make_ingestion_file(session)
    values: dict[str, Any] = {
        "txn_date": _DAY,
        "txn_id": ref,
        "original_reference_text": ref,
        "normalised_reference": "".join(c for c in ref.upper() if c.isalnum()),
        "amount": Decimal(minor) / 100,
        "amount_minor": minor,
        **extra,
    }
    txn = make_txn(f.id, source=source, **values)
    session.add(txn)
    session.flush()
    return txn


def _run(
    session: Session,
    internals: list[NormalisedTransaction],
    externals: list[NormalisedTransaction],
    *,
    fx: FxService | None = None,
    audit: bool = False,
    config: GroupMatchConfig | None = None,
) -> Any:
    return GroupMatchingStrategy(
        session,
        "run-group",
        config or GroupMatchConfig(),
        fx=fx,
        audit=AuditLogger(session) if audit else None,
        now=_NOW if audit else None,
    ).run(internals, externals)


# -- split: one internal, several externals -------------------------------------


def test_one_internal_settled_in_two_external_parts_is_a_split_group(db_session: Session) -> None:
    internal = _txn(db_session, "INTERNAL", "PAY-100", 100_000)
    parts = [
        _txn(db_session, "EXTERNAL", "PAY-100-1", 40_000),
        _txn(db_session, "EXTERNAL", "PAY-100-2", 60_000),
    ]

    outcome = _run(db_session, [internal], parts)

    assert (outcome.split_groups, outcome.netted_groups) == (1, 0)
    rows = db_session.scalars(select(MatchResult).order_by(MatchResult.created_at)).all()
    assert len(rows) == 2
    assert {r.match_type for r in rows} == {"SPLIT"}
    assert {r.status for r in rows} == {"AUTO_MATCHED"}
    assert len({r.match_group_id for r in rows}) == 1 and rows[0].match_group_id is not None
    assert {r.internal_transaction_id for r in rows} == {internal.id}
    assert {r.external_transaction_id for r in rows} == {p.id for p in parts}
    assert all(r.confidence == Decimal("0.950") and r.rule_id == "SPLIT_TRANSACTION" for r in rows)
    assert {t.match_status for t in (internal, *parts)} == {"MATCHED"}
    claims = db_session.scalars(select(MatchClaim).where(MatchClaim.status == "ACTIVE")).all()
    assert len(claims) == 3  # the single side is claimed once, each part once
    assert all(c.match_result_id is not None for c in claims)


def test_parts_that_do_not_sum_to_the_single_amount_are_not_grouped(db_session: Session) -> None:
    internal = _txn(db_session, "INTERNAL", "PAY-100", 100_000)
    parts = [
        _txn(db_session, "EXTERNAL", "PAY-100-1", 40_000),
        _txn(db_session, "EXTERNAL", "PAY-100-2", 59_000),
    ]
    outcome = _run(db_session, [internal], parts)
    assert outcome.group_count == 0
    assert db_session.query(MatchResult).count() == 0
    assert internal.match_status == "UNMATCHED"


def test_the_sum_may_differ_by_the_tolerance_but_no_more(db_session: Session) -> None:
    internal = _txn(db_session, "INTERNAL", "PAY-100", 100_000)
    near = [
        _txn(db_session, "EXTERNAL", "PAY-100-1", 40_000),
        _txn(db_session, "EXTERNAL", "PAY-100-2", 60_001),
    ]
    assert _run(db_session, [internal], near).split_groups == 1  # one paisa out: within tolerance

    other = _txn(db_session, "INTERNAL", "PAY-200", 100_000)
    far = [
        _txn(db_session, "EXTERNAL", "PAY-200-1", 40_000),
        _txn(db_session, "EXTERNAL", "PAY-200-2", 60_002),
    ]
    assert _run(db_session, [other], far).split_groups == 0


def test_parts_of_a_different_direction_are_not_grouped(db_session: Session) -> None:
    internal = _txn(db_session, "INTERNAL", "PAY-100", 100_000)
    parts = [
        _txn(db_session, "EXTERNAL", "PAY-100-1", 40_000),
        _txn(db_session, "EXTERNAL", "PAY-100-2", 60_000, direction="DR"),
    ]
    assert _run(db_session, [internal], parts).group_count == 0


def test_parts_outside_the_date_window_are_not_grouped(db_session: Session) -> None:
    internal = _txn(db_session, "INTERNAL", "PAY-100", 100_000)
    parts = [
        _txn(db_session, "EXTERNAL", "PAY-100-1", 40_000),
        _txn(db_session, "EXTERNAL", "PAY-100-2", 60_000, txn_date=date(2026, 3, 25)),
    ]
    assert _run(db_session, [internal], parts).group_count == 0


def test_a_group_can_be_found_among_unrelated_linked_candidates_by_subset_sum(
    db_session: Session,
) -> None:
    internal = _txn(db_session, "INTERNAL", "PAY-100", 100_000)
    parts = [
        _txn(db_session, "EXTERNAL", "PAY-100-1", 25_000),
        _txn(db_session, "EXTERNAL", "PAY-100-2", 75_000),
        _txn(db_session, "EXTERNAL", "PAY-100-3", 11_111),  # linked but not part of the sum
    ]
    outcome = _run(db_session, [internal], parts)
    assert outcome.split_groups == 1
    assert parts[2].match_status == "UNMATCHED"
    assert {parts[0].match_status, parts[1].match_status} == {"MATCHED"}


def test_two_groups_that_would_both_fit_are_ambiguous_and_nothing_is_claimed(
    db_session: Session,
) -> None:
    internal = _txn(db_session, "INTERNAL", "PAY-100", 50_000)
    parts = [  # 10+40 and 20+30 both reach 50
        _txn(db_session, "EXTERNAL", "PAY-100-1", 10_000),
        _txn(db_session, "EXTERNAL", "PAY-100-2", 40_000),
        _txn(db_session, "EXTERNAL", "PAY-100-3", 20_000),
        _txn(db_session, "EXTERNAL", "PAY-100-4", 30_000),
    ]
    outcome = _run(db_session, [internal], parts)
    assert (outcome.group_count, outcome.ambiguous_count) == (0, 1)
    assert db_session.query(MatchClaim).count() == 0


def test_too_many_linked_candidates_is_reported_not_searched(db_session: Session) -> None:
    internal = _txn(db_session, "INTERNAL", "PAY-100", 999_999)
    parts = [_txn(db_session, "EXTERNAL", f"PAY-100-{i}", 1_000 + i) for i in range(1, 8)]
    outcome = _run(db_session, [internal], parts, config=GroupMatchConfig(max_search_candidates=5))
    assert (outcome.group_count, outcome.too_complex_count) == (0, 1)


def test_the_member_cap_limits_the_group_size(db_session: Session) -> None:
    internal = _txn(db_session, "INTERNAL", "PAY-100", 30_000)
    parts = [_txn(db_session, "EXTERNAL", f"PAY-100-{i}", 10_000) for i in range(1, 4)]
    assert (
        _run(
            db_session, [internal], parts, config=GroupMatchConfig(max_split_members=2)
        ).group_count
        == 0
    )


# -- netted: several internals, one external ------------------------------------


def test_internals_netted_into_one_external_are_a_netted_group(db_session: Session) -> None:
    external = _txn(db_session, "EXTERNAL", "NET-20260315", 150_000)
    internals = [
        _txn(db_session, "INTERNAL", "NET-20260315-001", 50_000),
        _txn(db_session, "INTERNAL", "NET-20260315-002", 70_000),
        _txn(db_session, "INTERNAL", "NET-20260315-003", 30_000),
    ]

    outcome = _run(db_session, internals, [external])

    assert (outcome.split_groups, outcome.netted_groups) == (0, 1)
    rows = db_session.scalars(select(MatchResult)).all()
    assert len(rows) == 3
    assert {r.match_type for r in rows} == {"NETTED"}
    assert {r.external_transaction_id for r in rows} == {external.id}
    assert {t.match_status for t in (external, *internals)} == {"MATCHED"}
    claims = db_session.scalars(select(MatchClaim).where(MatchClaim.status == "ACTIVE")).all()
    assert sorted(c.role for c in claims) == ["EXTERNAL", "INTERNAL", "INTERNAL", "INTERNAL"]


def test_a_counterparty_link_groups_without_a_shared_reference(db_session: Session) -> None:
    party = {"counterparty_name_normalised": "ACME PRIVATE LIMITED"}
    external = _txn(db_session, "EXTERNAL", "SETTLE9", 100_000, **party)
    internals = [
        _txn(db_session, "INTERNAL", "A1", 60_000, **party),
        _txn(db_session, "INTERNAL", "B2", 40_000, **party),
    ]
    outcome = _run(db_session, internals, [external])
    assert outcome.netted_groups == 1
    row = db_session.scalars(select(MatchResult)).first()
    assert row is not None
    assert (row.confidence, row.matched_fields["link"]) == (Decimal("0.900"), "counterparty")


def test_unlinked_transactions_that_happen_to_sum_are_never_grouped(db_session: Session) -> None:
    external = _txn(db_session, "EXTERNAL", "SETTLE9", 100_000)
    internals = [
        _txn(db_session, "INTERNAL", "A1", 60_000),
        _txn(db_session, "INTERNAL", "B2", 40_000),
    ]
    assert _run(db_session, internals, [external]).group_count == 0


def test_a_claim_conflict_leaves_no_partial_group(db_session: Session) -> None:
    external = _txn(db_session, "EXTERNAL", "NET-20260315", 100_000)
    a = _txn(db_session, "INTERNAL", "NET-20260315-001", 60_000)
    b = _txn(db_session, "INTERNAL", "NET-20260315-002", 40_000)
    ClaimsService(db_session).claim(b.id, "INTERNAL")  # someone else holds b

    outcome = _run(db_session, [a, b], [external])

    assert (outcome.group_count, outcome.skipped_claim_conflict_count) == (0, 1)
    assert db_session.query(MatchClaim).filter_by(status="ACTIVE").count() == 1
    assert db_session.query(MatchResult).count() == 0
    assert (a.match_status, external.match_status) == ("UNMATCHED", "UNMATCHED")


def test_a_transaction_used_in_one_group_is_not_reused_in_another(db_session: Session) -> None:
    internal = _txn(db_session, "INTERNAL", "PAY-100", 100_000)
    parts = [
        _txn(db_session, "EXTERNAL", "PAY-100-1", 40_000),
        _txn(db_session, "EXTERNAL", "PAY-100-2", 60_000),
    ]
    twin = _txn(db_session, "INTERNAL", "PAY-100", 100_000)  # same reference, same amount
    outcome = _run(db_session, [internal, twin], parts)
    assert outcome.split_groups == 1
    assert twin.match_status == "UNMATCHED"


# -- cross-currency netting (B4.2) ----------------------------------------------

_GBP_142857_14 = 14_285_714


def _fx(session: Session, rate: str = "83.47") -> FxService:
    session.add(FxRate(rate_date=_DAY, currency="GBP", rate_inr=Decimal(rate), source="RBI"))
    session.flush()
    return FxService(session)


def _b42_internals(
    session: Session, total_paise: int, count: int = 23
) -> list[NormalisedTransaction]:
    base, extra = divmod(total_paise, count)
    return [
        _txn(
            session,
            "INTERNAL",
            f"NET-20260315-{i:03d}",
            base + (1 if i <= extra else 0),
            counterparty_name_normalised=None,
        )
        for i in range(1, count + 1)
    ]


def test_b42_the_corrected_figures_net_within_the_fx_tolerance(db_session: Session) -> None:
    fx = _fx(db_session)
    external = _txn(db_session, "EXTERNAL", "NET-20260315", _GBP_142857_14, currency="GBP")
    internals = _b42_internals(db_session, 1_199_999_976)  # INR 1,19,99,999.76

    outcome = _run(db_session, internals, [external], fx=fx, audit=True)

    assert outcome.netted_groups == 1
    rows = db_session.scalars(select(MatchResult)).all()
    assert len(rows) == 23
    first = rows[0]
    assert (first.match_type, first.rule_id, first.confidence) == (
        "NETTED",
        "NETTED_CROSS_CURRENCY",
        Decimal("0.900"),
    )
    fx_details = first.matched_fields["fx"]
    assert fx_details["foreign_currency"] == "GBP"
    assert fx_details["foreign_amount_minor"] == _GBP_142857_14
    assert fx_details["members_inr_minor"] == 1_199_999_976
    assert fx_details["reference_rate_inr"] == "83.47000000"
    assert fx_details["reference_rate_source"] == "RBI"
    assert fx_details["implied_rate_inr"] == "84.0000"
    assert Decimal("0.62") < Decimal(fx_details["variance_percent"]) < Decimal("0.64")
    assert "implied rate 84.0000" in first.rationale and "RBI" in first.rationale
    assert {t.match_status for t in (external, *internals)} == {"MATCHED"}


def test_b42_the_netting_is_audited_with_the_inr_sum_the_gbp_amount_and_the_rate(
    db_session: Session,
) -> None:
    fx = _fx(db_session)
    external = _txn(db_session, "EXTERNAL", "NET-20260315", _GBP_142857_14, currency="GBP")
    internals = _b42_internals(db_session, 1_199_999_976)

    _run(db_session, internals, [external], fx=fx, audit=True)

    entry = db_session.scalars(select(AuditLog).where(AuditLog.chain_id == MATCHING_CHAIN)).one()
    assert (entry.action_type, entry.actor_id) == ("MATCH", "group-matcher")
    after = entry.after_state
    assert after is not None
    assert after["fx"]["members_inr_minor"] == 1_199_999_976
    assert after["fx"]["foreign_amount_minor"] == _GBP_142857_14
    assert after["fx"]["reference_rate_inr"] == "83.47000000"
    assert len(entry.affected_records["member_transaction_ids"]) == 23
    assert verify_chain(db_session, MATCHING_CHAIN).ok is True


def test_b42_as_written_is_not_matched_because_the_variance_is_about_26_percent(
    db_session: Session,
) -> None:
    fx = _fx(db_session)
    external = _txn(db_session, "EXTERNAL", "NET-20260315", _GBP_142857_14, currency="GBP")
    internals = _b42_internals(db_session, 1_500_000_000)  # the PDF's INR 1.5 crore

    outcome = _run(db_session, internals, [external], fx=fx)

    assert outcome.group_count == 0
    assert external.match_status == "UNMATCHED"


def test_a_missing_fx_rate_is_counted_and_nothing_is_matched(db_session: Session) -> None:
    external = _txn(db_session, "EXTERNAL", "NET-20260315", _GBP_142857_14, currency="GBP")
    internals = _b42_internals(db_session, 1_199_999_976)
    outcome = _run(db_session, internals, [external], fx=FxService(db_session))
    assert (outcome.group_count, outcome.missing_fx_count) == (0, 1)


def test_cross_currency_without_an_fx_service_is_counted_not_guessed(db_session: Session) -> None:
    external = _txn(db_session, "EXTERNAL", "NET-20260315", _GBP_142857_14, currency="GBP")
    internals = _b42_internals(db_session, 1_199_999_976)
    assert _run(db_session, internals, [external]).missing_fx_count == 1


def test_the_interbank_tolerance_rejects_what_the_commercial_one_accepts(
    db_session: Session,
) -> None:
    fx = _fx(db_session)
    external = _txn(db_session, "EXTERNAL", "NET-20260315", _GBP_142857_14, currency="GBP")
    internals = _b42_internals(db_session, 1_199_999_976)  # 0.63% off
    strict = GroupMatchConfig(fx_tolerance_percent=Decimal("0.5"))
    assert _run(db_session, internals, [external], fx=fx, config=strict).group_count == 0


def test_group_results_are_replayable_and_unique_per_pairing(db_session: Session) -> None:
    internal = _txn(db_session, "INTERNAL", "PAY-100", 100_000)
    parts = [
        _txn(db_session, "EXTERNAL", "PAY-100-1", 40_000),
        _txn(db_session, "EXTERNAL", "PAY-100-2", 60_000),
    ]
    _run(db_session, [internal], parts)
    pairs = db_session.execute(
        select(MatchResult.internal_transaction_id, MatchResult.external_transaction_id)
    ).all()
    assert len(set(pairs)) == len(pairs) == 2
    assert all(
        isinstance(g, uuid.UUID) for (g,) in db_session.execute(select(MatchResult.match_group_id))
    )


def test_an_audit_logger_requires_a_clock() -> None:
    with pytest.raises(ValueError, match="now is required"):
        GroupMatchingStrategy(None, "r", GroupMatchConfig(), audit=object())  # type: ignore[arg-type]
