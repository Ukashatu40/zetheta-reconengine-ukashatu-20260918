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
      AND NOT EXISTS (
          SELECT 1 FROM match_claims c
          WHERE c.normalised_transaction_id = t.id AND c.status = 'ACTIVE'
      )
),
groups AS (
    SELECT reference, amount_minor, currency, direction
    FROM pool
    GROUP BY reference, amount_minor, currency, direction
    HAVING count(*) FILTER (WHERE source = 'INTERNAL') = 1
       AND count(*) FILTER (WHERE source = 'EXTERNAL') = 1
),
pairs AS (
    SELECT i.id AS internal_id, e.id AS external_id, g.reference, g.amount_minor,
           g.currency, g.direction, e.txn_date AS external_date
    FROM groups g
    JOIN pool i ON i.source = 'INTERNAL' AND i.reference = g.reference
               AND i.amount_minor = g.amount_minor AND i.currency = g.currency
               AND i.direction = g.direction
    JOIN pool e ON e.source = 'EXTERNAL' AND e.reference = g.reference
               AND e.amount_minor = g.amount_minor AND e.currency = g.currency
               AND e.direction = g.direction
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
    FROM pairs p
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
    UPDATE normalised_transactions t
    SET match_status = 'MATCHED'
    FROM (SELECT internal_transaction_id AS id FROM inserted
          UNION ALL
          SELECT external_transaction_id FROM inserted) m
    WHERE t.id = m.id AND t.match_status = 'UNMATCHED'
    RETURNING t.id
)
SELECT (SELECT count(*) FROM inserted) AS matched,
       (SELECT count(*) FROM claims) AS claimed,
       (SELECT count(*) FROM updated) AS updated
""")
_PAIR_AND_CLAIM = text(str(PAIR_AND_CLAIM_SQL))


class SetBasedExactError(RuntimeError):
    """The statement's claim or status-update counts did not match the pair count."""


class SetBasedExactStage:
    def __init__(self, session: Session, run_id: str) -> None:
        self._session = session
        self._run_id = run_id

    def run(self, bank_code: str) -> int:
        """Matches every unambiguous 1x1 key group and returns the number of pairs.

        Flushes first so pending rows are visible, and expires the session
        afterwards so already-loaded transactions reflect the raw UPDATE.
        Does not commit."""
        self._session.flush()
        row = self._session.execute(
            _PAIR_AND_CLAIM, {"bank_code": bank_code, "run_id": self._run_id}
        ).one()
        matched, claimed, updated = int(row.matched), int(row.claimed), int(row.updated)
        expected = 2 * matched
        if claimed != expected or updated != expected:
            raise SetBasedExactError(f"""{matched} pairs but {claimed} claims and
                {updated} status updates (expected {expected} each)""")
        if matched:
            self._session.expire_all()
        return matched
