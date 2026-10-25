# src/recon/reporting/generator.py
"""ReportGenerator (Day 6): the Daily Reconciliation Report, the Exception Ageing
Report, the Audit Compliance Report and, for B4.3, the Reconciliation Recovery
Report. Each returns a Report; render.py turns it into HTML, CSV or JSON.

Everything is a read. Money is shown in INR (the INR-equivalent of each exception,
AE-07); an exception in a currency with no available rate is counted separately as
"unvalued" instead of being added in at face value."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Date, and_, case, cast, exists, func, literal, select, text
from sqlalchemy.orm import Session

from recon.analytics.dashboard import OPEN_STATUSES
from recon.audit.verifier import verify_all_chains
from recon.config.models import BankConfig
from recon.persistence.models import (
    Alert,
    IngestionFile,
    MatchClaim,
    MatchResult,
    NormalisedTransaction,
    ReconciliationRun,
    ReconException,
    StatementBalance,
)
from recon.reporting.models import Cell, Report, ReportTable
from recon.reporting.render import format_inr

_AGE_BUCKETS = ("< 1 hour", "1-4 hours", "4-24 hours", "> 24 hours")
_OLDEST_LISTED = 20
_RESOLVED = ("RESOLVED", "WRITTEN_OFF")

_COVERED_IDS_SQL = text("""
    WITH covered AS (
        SELECT jsonb_array_elements_text(
                   affected_records -> 'normalised_transaction_ids')::uuid AS id
        FROM exceptions WHERE affected_records -> 'normalised_transaction_ids' IS NOT NULL
        UNION
        SELECT (affected_records ->> 'internal_transaction_id')::uuid
        FROM exceptions WHERE affected_records -> 'internal_transaction_id' IS NOT NULL
        UNION
        SELECT (affected_records ->> 'external_transaction_id')::uuid
        FROM exceptions WHERE affected_records -> 'external_transaction_id' IS NOT NULL
        UNION
        SELECT (affected_records ->> 'single_transaction_id')::uuid
        FROM exceptions WHERE affected_records -> 'single_transaction_id' IS NOT NULL
        UNION
        SELECT jsonb_array_elements_text(affected_records -> 'member_transaction_ids')::uuid
        FROM exceptions WHERE affected_records -> 'member_transaction_ids' IS NOT NULL
    )
    SELECT count(*) FROM normalised_transactions t
    WHERE t.txn_date BETWEEN :date_from AND :date_to
      AND t.match_status = 'UNMATCHED'
      AND NOT EXISTS (SELECT 1 FROM match_claims c
                      WHERE c.normalised_transaction_id = t.id AND c.status = 'ACTIVE')
      AND t.id NOT IN (SELECT id FROM covered)
    """)


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _percent(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 2) if denominator else 100.0


def _inr(minor: int | None) -> str:
    return format_inr(minor)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


class ReportGenerator:
    def __init__(self, session: Session, banks: Mapping[str, BankConfig], now: datetime) -> None:
        self._session = session
        self._banks = banks
        self._now = now

    # -- daily reconciliation ----------------------------------------------------------

    def daily(self, day: date) -> Report:
        start, end = _day_bounds(day)
        nt = NormalisedTransaction
        txn = {
            r.bank_code: r
            for r in self._session.execute(
                select(
                    nt.bank_code,
                    func.count().label("total"),
                    func.count().filter(nt.match_status == "MATCHED").label("matched"),
                )
                .where(nt.txn_date == day)
                .group_by(nt.bank_code)
            )
        }
        runs = {
            r.bank_code: r
            for r in self._session.execute(
                select(
                    ReconciliationRun.bank_code,
                    func.count().label("runs"),
                    func.count().filter(ReconciliationRun.status == "FAILED").label("failed"),
                )
                .where(ReconciliationRun.created_at >= start, ReconciliationRun.created_at < end)
                .group_by(ReconciliationRun.bank_code)
            )
        }
        created = ReconException.created_at
        on_time = case(
            (
                ReconException.resolved_at.is_not(None),
                ReconException.resolved_at <= ReconException.sla_deadline,
            ),
            else_=and_(
                ReconException.sla_breached_at.is_(None),
                ReconException.sla_deadline >= literal(self._now),
            ),
        )
        exceptions = {
            r.bank_code: r
            for r in self._session.execute(
                select(
                    ReconException.bank_code,
                    func.count().label("created"),
                    func.count().filter(ReconException.status.in_(OPEN_STATUSES)).label("open"),
                    func.count().filter(on_time).label("on_time"),
                )
                .where(created >= start, created < end)
                .group_by(ReconException.bank_code)
            )
        }
        carry = {
            r.bank_code: r
            for r in self._session.execute(
                select(
                    ReconException.bank_code,
                    func.count().label("open"),
                    func.coalesce(func.sum(ReconException.financial_impact_inr_minor), 0).label(
                        "inr"
                    ),
                )
                .where(created < start, ReconException.status.in_(OPEN_STATUSES))
                .group_by(ReconException.bank_code)
            )
        }

        banks = sorted(set(self._banks) | set(txn) | set(runs) | set(exceptions) | set(carry))
        rows: list[list[Cell]] = []
        totals: defaultdict[str, int] = defaultdict(int)
        for code in banks:
            t, ru, ex, cf = txn.get(code), runs.get(code), exceptions.get(code), carry.get(code)
            total, matched = (t.total, t.matched) if t else (0, 0)
            n_created, n_open, n_on_time = (ex.created, ex.open, ex.on_time) if ex else (0, 0, 0)
            carried, carried_inr = (cf.open, int(cf.inr)) if cf else (0, 0)
            rows.append(
                [
                    code,
                    total,
                    matched,
                    _rate(matched, total),
                    ru.runs if ru else 0,
                    n_created,
                    n_open,
                    _percent(n_on_time, n_created),
                    carried,
                    _inr(carried_inr),
                ]
            )
            for key, value in (
                ("total", total),
                ("matched", matched),
                ("created", n_created),
                ("open", n_open),
                ("on_time", n_on_time),
                ("carried", carried),
                ("carried_inr", carried_inr),
                ("failed_runs", ru.failed if ru else 0),
            ):
                totals[key] += value

        by_category = self._session.execute(
            select(ReconException.category, ReconException.severity, func.count())
            .where(created >= start, created < end)
            .group_by(ReconException.category, ReconException.severity)
            .order_by(func.count().desc(), ReconException.category)
        ).all()
        auto_resolved = (
            self._session.scalar(
                select(func.count())
                .select_from(ReconException)
                .where(
                    created >= start,
                    created < end,
                    ReconException.resolved_by == "system:auto-resolver",
                )
            )
            or 0
        )
        return Report(
            kind="daily",
            title=f"Daily Reconciliation Report, {day.isoformat()}",
            generated_at=self._now,
            parameters={"date": day.isoformat()},
            summary={
                "transactions": totals["total"],
                "matched": totals["matched"],
                "match_rate": _rate(totals["matched"], totals["total"]),
                "exceptions_created": totals["created"],
                "exceptions_auto_resolved": auto_resolved,
                "exceptions_still_open": totals["open"],
                "sla_compliance_percent": _percent(totals["on_time"], totals["created"]),
                "unresolved_carry_forward": totals["carried"],
                "carry_forward_value": _inr(totals["carried_inr"]),
                "failed_runs": totals["failed_runs"],
            },
            tables=[
                ReportTable(
                    "Per bank",
                    [
                        "Bank",
                        "Transactions",
                        "Matched",
                        "Match rate",
                        "Runs",
                        "Exceptions created",
                        "Still open",
                        "SLA compliance %",
                        "Carried forward",
                        "Carry-forward value",
                    ],
                    rows,
                ),
                ReportTable(
                    "Exceptions by category",
                    ["Category", "Severity", "Count"],
                    [[c, s, n] for c, s, n in by_category],
                ),
            ],
            notes=[
                "Transactions are those dated on the report day. Exceptions are those created "
                "on it (UTC). Carry-forward is every exception still open that was created "
                "before it.",
            ],
        )

    # -- exception ageing ----------------------------------------------------------------

    def exception_ageing(self) -> Report:
        age = literal(self._now) - ReconException.created_at
        bucket = case(
            (age < timedelta(hours=1), _AGE_BUCKETS[0]),
            (age < timedelta(hours=4), _AGE_BUCKETS[1]),
            (age < timedelta(hours=24), _AGE_BUCKETS[2]),
            else_=_AGE_BUCKETS[3],
        )
        rows = self._session.execute(
            select(
                bucket.label("bucket"),
                ReconException.bank_code,
                func.count().label("n"),
                func.coalesce(func.sum(ReconException.financial_impact_inr_minor), 0).label("inr"),
                func.count()
                .filter(ReconException.financial_impact_inr_minor.is_(None))
                .label("unvalued"),
            )
            .where(ReconException.status.in_(OPEN_STATUSES))
            .group_by(bucket, ReconException.bank_code)
        ).all()
        by_bucket: dict[str, list[int]] = {b: [0, 0, 0] for b in _AGE_BUCKETS}
        by_bank: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(_AGE_BUCKETS, 0))
        for r in rows:
            by_bucket[r.bucket][0] += r.n
            by_bucket[r.bucket][1] += int(r.inr)
            by_bucket[r.bucket][2] += r.unvalued
            by_bank[r.bank_code][r.bucket] += r.n
        total_open = sum(v[0] for v in by_bucket.values())
        total_inr = sum(v[1] for v in by_bucket.values())

        oldest = self._session.execute(
            select(
                ReconException.id,
                ReconException.bank_code,
                ReconException.category,
                ReconException.severity,
                ReconException.assigned_tier,
                ReconException.created_at,
                ReconException.financial_impact_inr_minor,
            )
            .where(ReconException.status.in_(OPEN_STATUSES))
            .order_by(ReconException.created_at, ReconException.id)
            .limit(_OLDEST_LISTED)
        ).all()
        return Report(
            kind="exception-ageing",
            title="Exception Ageing Report",
            generated_at=self._now,
            parameters={"as_of": self._now.isoformat()},
            summary={
                "open_exceptions": total_open,
                "open_value": _inr(total_inr),
                "older_than_24_hours": by_bucket[_AGE_BUCKETS[3]][0],
            },
            tables=[
                ReportTable(
                    "By age",
                    ["Age", "Open exceptions", "Financial impact", "Without an INR value"],
                    [[b, v[0], _inr(v[1]), v[2]] for b, v in by_bucket.items()],
                ),
                ReportTable(
                    "By bank and age",
                    ["Bank", *_AGE_BUCKETS],
                    [
                        [bank, *(counts[b] for b in _AGE_BUCKETS)]
                        for bank, counts in sorted(by_bank.items())
                    ],
                ),
                ReportTable(
                    f"Oldest {_OLDEST_LISTED} open exceptions",
                    ["Id", "Bank", "Category", "Severity", "Tier", "Age (hours)", "Impact"],
                    [
                        [
                            str(r.id),
                            r.bank_code,
                            r.category,
                            r.severity,
                            r.assigned_tier,
                            round((self._now - r.created_at).total_seconds() / 3600, 1),
                            _inr(r.financial_impact_inr_minor),
                        ]
                        for r in oldest
                    ],
                ),
            ],
            notes=[
                "Age is time since the exception was created. Impact is the INR-equivalent; an "
                "exception in a currency with no available rate has no INR value and is counted "
                "separately rather than added in at face value.",
            ],
        )

    # -- audit compliance -----------------------------------------------------------------------

    def audit_compliance(self, date_from: date, date_to: date) -> Report:
        nt = NormalisedTransaction
        in_period = nt.txn_date.between(date_from, date_to)
        total = self._session.scalar(select(func.count()).select_from(nt).where(in_period)) or 0
        matched = (
            self._session.scalar(
                select(func.count()).select_from(nt).where(in_period, nt.match_status == "MATCHED")
            )
            or 0
        )
        voided = (
            self._session.scalar(
                select(func.count()).select_from(nt).where(in_period, nt.match_status == "VOIDED")
            )
            or 0
        )
        held = (
            self._session.scalar(
                select(func.count())
                .select_from(nt)
                .where(
                    in_period,
                    nt.match_status == "UNMATCHED",
                    exists().where(
                        MatchClaim.normalised_transaction_id == nt.id, MatchClaim.status == "ACTIVE"
                    ),
                )
            )
            or 0
        )
        unmatched_uncovered = int(
            self._session.scalar(_COVERED_IDS_SQL, {"date_from": date_from, "date_to": date_to})
            or 0
        )

        created_day = cast(ReconException.created_at, Date)
        exc_in_period = created_day.between(date_from, date_to)
        exceptions_total = (
            self._session.scalar(
                select(func.count()).select_from(ReconException).where(exc_in_period)
            )
            or 0
        )
        resolved = (
            self._session.scalar(
                select(func.count())
                .select_from(ReconException)
                .where(exc_in_period, ReconException.status.in_(_RESOLVED))
            )
            or 0
        )
        open_now = (
            self._session.scalar(
                select(func.count())
                .select_from(ReconException)
                .where(exc_in_period, ReconException.status.in_(OPEN_STATUSES))
            )
            or 0
        )
        unaddressed = (
            self._session.scalar(
                select(func.count())
                .select_from(ReconException)
                .where(
                    exc_in_period,
                    ReconException.status.in_(OPEN_STATUSES),
                    ReconException.sla_deadline < literal(self._now),
                    ReconException.sla_breached_at.is_(None),
                )
            )
            or 0
        )

        chains = verify_all_chains(self._session)
        chain_rows: list[list[Cell]] = [
            [cid, r.entries_checked, "intact" if r.ok else f"BROKEN: {r.failure_kind} {r.detail}"]
            for cid, r in sorted(chains.items())
        ]
        chains_ok = all(r.ok for r in chains.values())

        files = self._session.execute(
            select(IngestionFile.integrity_status, func.count())
            .where(cast(IngestionFile.ingested_at, Date).between(date_from, date_to))
            .group_by(IngestionFile.integrity_status)
        ).all()
        file_counts: dict[str | None, int] = {}
        for status, n in files:
            file_counts[status] = n
        tampered = file_counts.get("TAMPERED", 0) + file_counts.get("MISSING", 0)
        unbalanced = (
            self._session.scalar(
                select(func.count())
                .select_from(StatementBalance)
                .where(
                    cast(StatementBalance.created_at, Date).between(date_from, date_to),
                    StatementBalance.balanced.is_(False),
                )
            )
            or 0
        )
        open_critical = (
            self._session.scalar(
                select(func.count())
                .select_from(Alert)
                .where(Alert.status == "OPEN", Alert.severity == "CRITICAL")
            )
            or 0
        )

        failures: list[str] = []
        if unmatched_uncovered:
            failures.append(f"{unmatched_uncovered} unmatched transaction(s) have no exception")
        if unaddressed:
            failures.append(
                f"{unaddressed} exception(s) are past their SLA and were never escalated"
            )
        if not chains_ok:
            failures.append("the audit trail failed hash verification")
        if tampered:
            failures.append(f"{tampered} stored file(s) failed re-verification")
        if unbalanced:
            failures.append(f"{unbalanced} statement(s) do not balance")
        return Report(
            kind="audit-compliance",
            title=f"Audit Compliance Report, {date_from.isoformat()} to {date_to.isoformat()}",
            generated_at=self._now,
            parameters={"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
            summary={
                "transactions_in_period": total,
                "matched": matched,
                "voided_duplicates": voided,
                "held_for_review": held,
                "unmatched_without_an_exception": unmatched_uncovered,
                "exceptions_in_period": exceptions_total,
                "exceptions_resolved": resolved,
                "exceptions_open": open_now,
                "exceptions_past_sla_not_escalated": unaddressed,
                "audit_trail_intact": chains_ok,
                "files_failing_reverification": tampered,
                "statements_not_balancing": unbalanced,
                "open_critical_alerts": open_critical,
            },
            tables=[
                ReportTable("Audit chains", ["Chain", "Entries verified", "Status"], chain_rows),
                ReportTable(
                    "Stored files by integrity status",
                    ["Status", "Files"],
                    [
                        [k or "not yet verified", n]
                        for k, n in sorted(file_counts.items(), key=lambda kv: str(kv[0]))
                    ],
                ),
                ReportTable("Failures", ["Finding"], [[f] for f in failures]),
            ],
            verdict="PASS" if not failures else "FAIL",
            notes=[
                "PASS means every transaction in the period is matched, in review, voided, or "
                "covered by an exception; no exception is past its SLA unescalated; every audit "
                "chain re-verifies; and no stored file or statement failed its check.",
                "Hash verification cannot detect deletion of the newest audit entries unless a "
                "chain head exported earlier is supplied (GET /api/v1/audit/anchors).",
            ],
        )

    # -- recovery (B4.3) -------------------------------------------------------------------------

    def recovery(self, window_hours: int = 24) -> Report:
        since = self._now - timedelta(hours=window_hours)
        hour = func.date_trunc("hour", MatchResult.created_at)
        per_hour = self._session.execute(
            select(hour.label("hour"), func.count().label("n"))
            .where(
                MatchResult.created_at >= since,
                MatchResult.status.in_(("AUTO_MATCHED", "CONFIRMED")),
            )
            .group_by(hour)
            .order_by(hour)
        ).all()
        matched_in_window = sum(r.n for r in per_hour)
        nt = NormalisedTransaction
        backlog_rows = self._session.execute(
            select(nt.bank_code, func.count())
            .where(
                nt.match_status == "UNMATCHED",
                ~exists().where(
                    MatchClaim.normalised_transaction_id == nt.id, MatchClaim.status == "ACTIVE"
                ),
            )
            .group_by(nt.bank_code)
            .order_by(nt.bank_code)
        ).all()
        backlog = sum(n for _, n in backlog_rows)

        total_txns = self._session.scalar(select(func.count()).select_from(nt)) or 0
        total_exceptions = (
            self._session.scalar(select(func.count()).select_from(ReconException)) or 0
        )
        exception_ratio = total_exceptions / total_txns if total_txns else 0.0
        rate = matched_in_window / window_hours
        projected = round(backlog / rate, 1) if rate > 0 else None
        runs = self._session.execute(
            select(ReconciliationRun.mode, ReconciliationRun.status, func.count())
            .where(ReconciliationRun.created_at >= since)
            .group_by(ReconciliationRun.mode, ReconciliationRun.status)
            .order_by(ReconciliationRun.mode, ReconciliationRun.status)
        ).all()
        return Report(
            kind="recovery",
            title="Reconciliation Recovery Report",
            generated_at=self._now,
            parameters={"window_hours": window_hours, "as_of": self._now.isoformat()},
            summary={
                "backlog_unmatched_transactions": backlog,
                "reconciled_in_window": matched_in_window,
                "reconciled_per_hour": round(rate, 1),
                "projected_hours_to_clear": projected,
                "estimated_exceptions_from_backlog": math.ceil(backlog * exception_ratio),
                "historical_exception_rate": round(exception_ratio, 4),
            },
            tables=[
                ReportTable(
                    "Reconciled per hour",
                    ["Hour (UTC)", "Matched"],
                    [[r.hour.isoformat(), r.n] for r in per_hour],
                ),
                ReportTable(
                    "Backlog by bank", ["Bank", "Unmatched"], [[b, n] for b, n in backlog_rows]
                ),
                ReportTable(
                    "Runs in the window",
                    ["Mode", "Status", "Runs"],
                    [[m, s, n] for m, s, n in runs],
                ),
            ],
            notes=[
                "Projected time divides today's backlog by the average matches per hour over the "
                "window, so it is only as good as the recent rate; it is None when nothing was "
                "matched in the window.",
                "Estimated exceptions apply the all-time ratio of exceptions to transactions to "
                "the backlog.",
            ],
        )
