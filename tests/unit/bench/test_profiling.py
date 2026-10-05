# tests/unit/bench/test_profiling.py
from __future__ import annotations

import pytest

from recon.bench.profiling import classify_statement


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("INSERT INTO match_results (a) VALUES (%(a)s)", ("INSERT", "match_results")),
        (
            "UPDATE normalised_transactions SET match_status=%(s)s WHERE id=%(i)s",
            ("UPDATE", "normalised_transactions"),
        ),
        ("SELECT x FROM normalised_transactions WHERE y", ("SELECT", "normalised_transactions")),
        ("SELECT e.from_tier FROM exception_events e", ("SELECT", "exception_events")),
        ("INSERT INTO audit.audit_log (a) VALUES (1)", ("INSERT", "audit.audit_log")),
        ("SELECT pg_advisory_xact_lock(1)", ("SELECT", "-")),
        ("SAVEPOINT sa_savepoint_1", ("SAVEPOINT", "-")),
        ("RELEASE SAVEPOINT sa_savepoint_1", ("RELEASE", "-")),
        ("  select 1", ("SELECT", "-")),
        ("", ("EMPTY", "-")),
    ],
)
def test_classify_statement(sql: str, expected: tuple[str, str]) -> None:
    assert classify_statement(sql) == expected
