# tests/integration/test_set_based_exact.py
from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.bench.dataset import generate
from recon.bench.runner import BANK_CODE, insert_records
from recon.config.matching_loader import load_matching_config
from recon.matching.blocking.candidates import BlockingConfig
from recon.matching.claims import ClaimsService
from recon.matching.orchestrator import MatchingOrchestrator
from recon.matching.strategies.exact_set_based import SetBasedExactStage
from recon.paths import config_dir
from recon.persistence.models import MatchClaim, MatchResult, NormalisedTransaction
from tests.integration.factories import make_ingestion_file, make_txn


def _stage(session: Session, run_id: str = "sb-1") -> SetBasedExactStage:
    return SetBasedExactStage(session, run_id)


def _pair(
    session: Session, external: dict[str, Any] | None = None, **shared: Any
) -> tuple[NormalisedTransaction, NormalisedTransaction]:
    f = make_ingestion_file(session)
    internal = make_txn(f.id, source="INTERNAL", **shared)
    other = make_txn(f.id, source="EXTERNAL", **{**shared, **(external or {})})
    session.add_all([internal, other])
    session.flush()
    return internal, other


# -- the stage on its own ----------------------------------------------------------


def test_a_one_to_one_group_is_matched_with_the_same_record_python_would_write(
    db_session: Session,
) -> None:
    internal, external = _pair(db_session, external={"txn_date": date(2026, 3, 25)})

    assert _stage(db_session).run("HDFC") == 1

    result = db_session.scalars(select(MatchResult)).one()
    assert (result.match_type, result.status, str(result.confidence)) == (
        "EXACT",
        "AUTO_MATCHED",
        "1.000",
    )
    assert (result.candidate_count, result.rule_id, result.weights_version) == (1, None, None)
    assert result.matched_on_date == date(
        2026, 3, 25
    )  # the external's date; date is not in the key
    assert result.hard_constraints_passed is True
    assert result.field_scores == {
        "reference": 1.0,
        "amount": 1.0,
        "currency": 1.0,
        "direction": 1.0,
    }
    assert result.matched_fields == {
        "normalised_reference": "REF001",
        "amount_minor": 150_000,
        "currency": "INR",
        "direction": "CR",
    }
    assert result.rationale == "Exact match on (reference, amount, currency, direction)."
    assert (internal.match_status, external.match_status) == (
        "MATCHED",
        "MATCHED",
    )  # session was refreshed
    claims = db_session.scalars(
        select(MatchClaim).where(MatchClaim.match_result_id == result.id)
    ).all()
    assert {(c.role, c.status) for c in claims} == {("INTERNAL", "ACTIVE"), ("EXTERNAL", "ACTIVE")}
    assert {c.normalised_transaction_id for c in claims} == {internal.id, external.id}


def test_groups_with_more_than_one_candidate_are_left_for_the_python_stage(
    db_session: Session,
) -> None:
    f = make_ingestion_file(db_session)
    two_internals = [make_txn(f.id, source="INTERNAL", normalised_reference="A") for _ in range(2)]
    one_external = make_txn(f.id, source="EXTERNAL", normalised_reference="A")
    one_internal = make_txn(f.id, source="INTERNAL", normalised_reference="B")
    two_externals = [make_txn(f.id, source="EXTERNAL", normalised_reference="B") for _ in range(2)]
    db_session.add_all([*two_internals, one_external, one_internal, *two_externals])
    db_session.flush()

    assert _stage(db_session).run("HDFC") == 0
    assert db_session.query(MatchResult).count() == 0
    assert {
        t.match_status for t in [*two_internals, one_external, one_internal, *two_externals]
    } == {"UNMATCHED"}


@pytest.mark.parametrize("reference", ["", "   "])
def test_a_blank_reference_is_never_matched(db_session: Session, reference: str) -> None:
    _pair(db_session, normalised_reference=reference)
    assert _stage(db_session).run("HDFC") == 0


@pytest.mark.parametrize(
    "external_change",
    [
        {"amount_minor": 150_001},
        {"currency": "USD"},
        {"direction": "DR"},
        {"normalised_reference": "OTHER"},
    ],
)
def test_every_part_of_the_key_must_match(
    db_session: Session, external_change: dict[str, Any]
) -> None:
    _pair(db_session, external=external_change)
    assert _stage(db_session).run("HDFC") == 0


def test_an_actively_claimed_transaction_is_excluded(db_session: Session) -> None:
    internal, _ = _pair(db_session)
    ClaimsService(db_session).claim(internal.id, "INTERNAL")
    assert _stage(db_session).run("HDFC") == 0


def test_other_banks_are_untouched(db_session: Session) -> None:
    other_internal, _ = _pair(db_session, bank_code="ICICI")
    assert _stage(db_session).run("HDFC") == 0
    assert other_internal.match_status == "UNMATCHED"


def test_a_second_run_finds_nothing_new(db_session: Session) -> None:
    _pair(db_session)
    assert _stage(db_session, "first").run("HDFC") == 1
    assert _stage(db_session, "second").run("HDFC") == 0
    assert db_session.query(MatchResult).count() == 1


# -- equivalence with the Python path -----------------------------------------------

_BLOCKING = BlockingConfig(amount_bucket_width_minor=10_000, date_window_days=2)


def _extra_groups(session: Session) -> list[NormalisedTransaction]:
    f = make_ingestion_file(session)

    def txn(source: str, reference: str, day: int) -> NormalisedTransaction:
        return make_txn(
            f.id, source=source, bank_code=BANK_CODE, normalised_reference=reference,
            txn_date=date(2026, 3, day),
        )  # fmt: skip

    return [
        txn("INTERNAL", "XA", 1), txn("INTERNAL", "XA", 5),
        txn("EXTERNAL", "XA", 4), txn("EXTERNAL", "XA", 6),  # 2x2
        txn("INTERNAL", "XB", 10), txn("EXTERNAL", "XB", 10),
        txn("EXTERNAL", "XB", 10),  # 1x2: duplicate settlement
        txn("INTERNAL", "XC", 12), txn("INTERNAL", "XC", 13),
        txn("EXTERNAL", "XC", 12),  # 2x1
        txn("INTERNAL", "  ", 14), txn("EXTERNAL", "  ", 14),  # blank reference
        txn("INTERNAL", "XD", 15), txn("EXTERNAL", "XD", 15),  # plain 1x1
        txn("INTERNAL", "XE", 16), txn("EXTERNAL", "XE", 16),  # internal pre-claimed below
    ]  # fmt: skip


def _snapshot(session: Session, *, set_based: bool) -> dict[str, object]:
    """Runs the orchestrator inside a savepoint, captures everything observable, rolls back."""
    run_id = f"diff-{set_based}"
    nested = session.begin_nested()
    try:
        outcome = MatchingOrchestrator(
            session, run_id, _BLOCKING, 100,
            load_matching_config(config_dir() / "matching" / "weights.yaml"),
            set_based_exact=set_based,
        ).run(BANK_CODE)  # fmt: skip
        session.flush()
        results = sorted(
            (
                str(r.internal_transaction_id),
                str(r.external_transaction_id),
                r.match_type, r.status,
                r.candidate_count,
                str(r.confidence),
                r.matched_on_date.isoformat(),
                r.rationale,
                str(r.rule_id),
                str(r.weights_version), r.hard_constraints_passed,
                json.dumps(r.field_scores, sort_keys=True),
                json.dumps(r.matched_fields, sort_keys=True),
            )
            for r in session.scalars(select(MatchResult).where(MatchResult.run_id == run_id))
        )  # fmt: skip
        statuses = {
            str(i): s
            for i, s in session.execute(
                select(NormalisedTransaction.id, NormalisedTransaction.match_status)
            )
        }
        claims = sorted(
            (str(c.normalised_transaction_id), c.role, c.status)
            for c in session.scalars(select(MatchClaim))
        )
        m = outcome.metrics
        counters = (
            outcome.exact.matched_count,
            outcome.exact.skipped_no_key_count,
            outcome.exact.skipped_claim_conflict_count,
            outcome.fuzzy.auto_matched_count,
            outcome.fuzzy.review_count,
            outcome.fuzzy.skipped_claim_conflict_count,
            outcome.internal_pool_size_before_exact,
            outcome.external_pool_size_before_exact,
            outcome.internal_pool_size_before_fuzzy,
            outcome.external_pool_size_before_fuzzy,
            m.exact_match_rate_of_total,
            m.exact_match_rate_of_matchable,
            m.overall_match_rate_of_total,
        )  # fmt: skip
        return {"results": results, "statuses": statuses, "claims": claims, "counters": counters}
    finally:
        nested.rollback()


def test_set_based_and_python_exact_leave_identical_state(db_session: Session) -> None:
    insert_records(db_session, generate(200, 5))
    extras = _extra_groups(db_session)
    db_session.add_all(extras)
    db_session.flush()
    pre_claimed = next(
        t for t in extras if t.normalised_reference == "XE" and t.source == "INTERNAL"
    )
    ClaimsService(db_session).claim(pre_claimed.id, "INTERNAL")

    python_only = _snapshot(db_session, set_based=False)
    hybrid = _snapshot(db_session, set_based=True)

    assert len(python_only["results"]) > 100  # type: ignore[arg-type]  # the comparison is not vacuous
    for key in ("results", "statuses", "claims", "counters"):
        assert hybrid[key] == python_only[key], key


def test_orchestrator_totals_cover_both_exact_stages(db_session: Session) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all(
        [
            make_txn(f.id, source="INTERNAL", normalised_reference="A"),
            make_txn(f.id, source="EXTERNAL", normalised_reference="A"),  # 1x1: set-based
            make_txn(f.id, source="INTERNAL", normalised_reference="B", txn_date=date(2026, 3, 1)),
            make_txn(f.id, source="INTERNAL", normalised_reference="B", txn_date=date(2026, 3, 15)),
            make_txn(
                f.id, source="EXTERNAL", normalised_reference="B", txn_date=date(2026, 3, 16)
            ),  # 2x1: Python
        ]
    )
    db_session.flush()

    outcome = MatchingOrchestrator(
        db_session, "totals", _BLOCKING, 100,
        load_matching_config(config_dir() / "matching" / "weights.yaml"),
        set_based_exact=True,
    ).run("HDFC")  # fmt: skip

    assert outcome.exact.matched_count == 2
    assert (outcome.internal_pool_size_before_exact, outcome.external_pool_size_before_exact) == (
        3,
        2,
    )
    assert (outcome.internal_pool_size_before_fuzzy, outcome.external_pool_size_before_fuzzy) == (
        1,
        0,
    )
    chosen = db_session.scalars(
        select(MatchResult).where(
            MatchResult.matched_fields["normalised_reference"].as_string() == "B"
        )
    ).one()
    closest = db_session.scalars(
        select(NormalisedTransaction.id).where(
            NormalisedTransaction.normalised_reference == "B",
            NormalisedTransaction.txn_date == date(2026, 3, 15),
        )
    ).one()
    assert chosen.internal_transaction_id == closest  # R47 still decides the collision group
