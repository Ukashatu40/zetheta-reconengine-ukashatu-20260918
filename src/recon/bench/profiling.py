"""SQL statement profiling for benchmark runs.

Counts the statements sent on one connection and the client-side time spent
inside cursor.execute (round trip plus database time), grouped by verb and
table, with the slowest single statement of each kind. It exists to answer
"where does the time go" from measurement. Statements are classified by first
keyword; executemany counts as one statement carrying several parameter sets.
"""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection

_TABLE = re.compile(r"(?:INTO|UPDATE|FROM)\s+\"?([A-Za-z_][\w.]*)\"?", re.IGNORECASE)
_START_KEY = "recon_profile_starts"
_NO_TABLE_VERBS = frozenset({"SAVEPOINT", "RELEASE", "ROLLBACK", "BEGIN", "COMMIT"})


def classify_statement(statement: str) -> tuple[str, str]:
    """(verb, table) for a SQL statement; the table is '-' when none applies."""
    text = statement.strip()
    verb = text.split(None, 1)[0].upper() if text else "EMPTY"
    if verb in _NO_TABLE_VERBS:
        return verb, "-"
    match = _TABLE.search(text)
    return verb, match.group(1) if match else "-"


@dataclass(frozen=True, slots=True)
class StatementStat:
    count: int
    seconds: float
    max_seconds: float


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    statements: int
    statements_per_persisted_result: float | None
    db_wait_seconds: float
    by_statement: dict[str, StatementStat]


class StatementProfile:
    def __init__(self) -> None:
        self.counts: Counter[tuple[str, str]] = Counter()
        self.parameter_sets: Counter[tuple[str, str]] = Counter()
        self.seconds: defaultdict[tuple[str, str], float] = defaultdict(float)
        self.slowest: defaultdict[tuple[str, str], float] = defaultdict(float)
        self.db_seconds = 0.0
        self._connection: Connection | None = None
        # Kept as attributes: event.remove needs the same function objects that were registered.
        self._before_listener: Callable[..., None] = self._before
        self._after_listener: Callable[..., None] = self._after

    def attach(self, connection: Connection) -> None:
        if self._connection is not None:
            raise RuntimeError("profile is already attached to a connection")
        event.listen(connection, "before_cursor_execute", self._before_listener)
        event.listen(connection, "after_cursor_execute", self._after_listener)
        self._connection = connection

    def detach(self) -> None:
        if self._connection is None:
            return
        event.remove(self._connection, "before_cursor_execute", self._before_listener)
        event.remove(self._connection, "after_cursor_execute", self._after_listener)
        self._connection = None

    def summary(self, persisted_results: int) -> ProfileSummary:
        total = sum(self.counts.values())
        ordered = sorted(self.counts, key=lambda key: (-self.seconds[key], key))
        return ProfileSummary(
            statements=total,
            statements_per_persisted_result=(
                total / persisted_results if persisted_results else None
            ),
            db_wait_seconds=self.db_seconds,
            by_statement={
                f"{verb} {table}": StatementStat(
                    self.counts[(verb, table)],
                    self.seconds[(verb, table)],
                    self.slowest[(verb, table)],
                )
                for verb, table in ordered
            },
        )

    def _before(
        self,
        conn: Connection,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        executemany: bool,
    ) -> None:
        key = classify_statement(statement)
        conn.info.setdefault(_START_KEY, []).append((time.perf_counter(), key))
        self.counts[key] += 1
        batch = len(parameters) if executemany and isinstance(parameters, list | tuple) else 1
        self.parameter_sets[key] += batch

    def _after(
        self,
        conn: Connection,
        _cursor: Any,
        _statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        started, key = conn.info[_START_KEY].pop()
        elapsed = time.perf_counter() - started
        self.db_seconds += elapsed
        self.seconds[key] += elapsed
        self.slowest[key] = max(self.slowest[key], elapsed)
