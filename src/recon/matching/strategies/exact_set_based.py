# src/recon/matching/strategies/exact_set_based.py
"""Set-based exact matching for unambiguous key groups (DD-21).

One SQL statement pairs every key group that has exactly one available
internal and one available external, writes the EXACT match_results rows,
inserts both claims and marks both transactions MATCHED. Key groups with more
candidates on either side are left untouched for ExactMatchingStrategy, which
owns the R47 disambiguation.

The statement avoids JSON literals (a ':' followed by a digit would be read as
a bind parameter) and uses jsonb_build_object instead.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

PAIR_AND_CLAIM_SQL = text(r"""
WITH pool AS (
    SELECT t.id, t.source, t.normalised_reference AS reference, t.amount_minor,
           t.currency, t.direction, t.txn_date
    FROM normalised_transactions t
    WHERE t.bank_code = :bank_code
      AND t.match_status = 'UNMATCHED'
      AND t.normalised_reference ~ '\S'
      AND (CAST(:date_from AS DATE) IS NULL OR t.txn_date >= CAST(:date_from AS DATE))
      AND (CAST(:date_to AS DATE) IS NULL OR t.txn_date <= CAST(:date_to AS DATE))
      AND NOT EXISTS (
          SELECT 1 FROM match_claims c
          WHERE c.normalised_transaction_id = t.id AND c.status = 'ACTIVE'
      )
),
pairs AS (
    SELECT (array_agg(id) FILTER (WHERE source = 'INTERNAL'))[1] AS internal_id,
           (array_agg(id) FILTER (WHERE source = 'EXTERNAL'))[1] AS external_id,
           (array_agg(txn_date) FILTER (WHERE source = 'INTERNAL'))[1] AS internal_date,
           (array_agg(txn_date) FILTER (WHERE source = 'EXTERNAL'))[1] AS external_date,
           reference, amount_minor, currency, direction
    FROM pool
    GROUP BY reference, amount_minor, currency, direction
    HAVING count(*) FILTER (WHERE source = 'INTERNAL') = 1
       AND count(*) FILTER (WHERE source = 'EXTERNAL') = 1
),
allowed AS (
    -- NOT IN, not NOT EXISTS: it is planned as a hashed subplan, evaluated once. The correlated
    -- form was planned as a nested loop that scanned match_results once per candidate pair.
    SELECT p.* FROM pairs p
    WHERE (p.internal_id, p.external_id) NOT IN (
        SELECT r.internal_transaction_id, r.external_transaction_id
        FROM match_results r
        WHERE r.status = 'REJECTED'
    )
),
inserted AS (
    INSERT INTO match_results (
        run_id, match_type, status, confidence, internal_transaction_id,
        external_transaction_id, field_scores, matched_fields, hard_constraints_passed,
        rule_id, candidate_count, rationale, matched_on_date, weights_version
    )
    SELECT CAST(:run_id AS VARCHAR(100)), 'EXACT', 'AUTO_MATCHED', 1.000,
           p.internal_id, p.external_id,
           jsonb_build_object('reference', 1.0, 'amount', 1.0, 'currency', 1.0, 'direction', 1.0),
           jsonb_build_object('normalised_reference', p.reference, 'amount_minor', p.amount_minor,
                              'currency', p.currency, 'direction', p.direction),
           true, NULL, 1, 'Exact match on (reference, amount, currency, direction).',
           p.external_date, NULL
    FROM allowed p
    RETURNING id, internal_transaction_id, external_transaction_id
),
claims AS (
    INSERT INTO match_claims (normalised_transaction_id, match_result_id, role, status)
    SELECT internal_transaction_id, id, 'INTERNAL', 'ACTIVE' FROM inserted
    UNION ALL
    SELECT external_transaction_id, id, 'EXTERNAL', 'ACTIVE' FROM inserted
    RETURNING normalised_transaction_id
),
updated AS (
    -- Joined on the partition key as well as the id, so each row is found in one monthly
    -- partition instead of probing all of them.
    UPDATE normalised_transactions t
    SET match_status = 'MATCHED'
    FROM (SELECT internal_id AS id, internal_date AS txn_date FROM allowed
          UNION ALL
          SELECT external_id, external_date FROM allowed) m
    WHERE t.id = m.id AND t.txn_date = m.txn_date AND t.match_status = 'UNMATCHED'
    RETURNING t.id
)
SELECT (SELECT count(*) FROM inserted) AS matched,
       (SELECT count(*) FROM claims) AS claimed,
       (SELECT count(*) FROM updated) AS updated
""")
_PAIR_AND_CLAIM = text(str(PAIR_AND_CLAIM_SQL))
_ANALYZE_STATEMENTS = (
    text("ANALYZE normalised_transactions"),
    text("ANALYZE match_claims"),
    # Without statistics the planner assumes match_results is tiny and probes it with a
    # sequential scan once per candidate pair for the rejected-pair check (10 million buffer
    # hits at 20,000 pairs), instead of using the partial index on rejected pairs.
    text("ANALYZE match_results"),
)


class SetBasedExactError(RuntimeError):
    """The statement's claim or status-update counts did not match the pair count."""


class SetBasedExactStage:
    def __init__(self, session: Session, run_id: str) -> None:
        self._session = session
        self._run_id = run_id

    def run(
        self, bank_code: str, *, date_from: date | None = None, date_to: date | None = None
    ) -> int:
        """Matches every unambiguous 1x1 key group and returns the number of pairs.

        Flushes first so pending rows are visible, and expires the session
        afterwards so already-loaded transactions reflect the raw UPDATE.
        Does not commit."""
        self._session.flush()
        # Rows loaded moments ago have no planner statistics until autovacuum catches up. At
        # 20,000 entities the unanalysed plan ran the status UPDATE as a per-row bitmap scan
        # (about 38 s against 0.9 s analysed; DD-21).
        for statement in _ANALYZE_STATEMENTS:
            self._session.execute(statement)
        row = self._session.execute(
            _PAIR_AND_CLAIM,
            {
                "bank_code": bank_code,
                "run_id": self._run_id,
                "date_from": date_from,
                "date_to": date_to,
            },
        ).one()
        matched, claimed, updated = int(row.matched), int(row.claimed), int(row.updated)
        expected = 2 * matched
        if claimed != expected or updated != expected:
            raise SetBasedExactError(f"""{matched} pairs but {claimed} claims and
                {updated} status updates (expected {expected} each)""")
        if matched:
            self._session.expire_all()
        return matched
