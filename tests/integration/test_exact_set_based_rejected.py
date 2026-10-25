"""The set-based exact stage and the rejected-pair check (planner behaviour, IB-17)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from recon.matching.strategies.exact_set_based import PAIR_AND_CLAIM_SQL
from tests.integration.factories import make_ingestion_file, make_txn


def test_the_rejected_pair_check_is_a_hashed_subplan_not_a_per_pair_scan(
    db_session: Session,
) -> None:
    """The correlated NOT EXISTS form was planned as a nested loop that scanned match_results once
    per candidate pair (10 million buffer hits at 20,000 pairs). The plan must not do that."""
    f = make_ingestion_file(db_session)
    db_session.add_all([make_txn(f.id, source="INTERNAL"), make_txn(f.id, source="EXTERNAL")])
    db_session.flush()
    db_session.execute(text("ANALYZE normalised_transactions"))
    plan = "\n".join(
        row[0]
        for row in db_session.execute(
            text("EXPLAIN " + str(PAIR_AND_CLAIM_SQL)),
            {"bank_code": "HDFC", "run_id": "x", "date_from": None, "date_to": None},
        )
    )
    assert "hashed SubPlan" in plan
