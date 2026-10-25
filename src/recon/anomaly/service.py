# src/recon/anomaly/service.py
"""AnomalyService: runs the Case Study C2 rules and the operational alerts, and
raises them into the alerts table.

scan_ingestion_file   Amount Deviation and Velocity against each counterparty's
                      rolling history, the data-volume check, then updates that
                      history. Runs once per file (anomaly_scanned_at).
check_absence         Absence Alert: a bank that should have settled by now has not.
check_settlement_uniqueness   B4.4: a file whose settlements were already ingested.
evaluate_run          A1.3 / B4.4 / C1: exception rate, suspiciously high match
                      rate, exception backlog.

Alerts are idempotent by dedupe_key: raising one twice leaves one row.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy import insert as core_insert
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from recon.anomaly.config import AnomalyConfig
from recon.anomaly.rules import (
    amount_deviation,
    expected_settlement_overdue,
    is_date_only,
    velocity_breach,
)
from recon.anomaly.stats import RollingStats, first_observation, observe
from recon.calendar import default_calendar
from recon.config.models import BankConfig
from recon.ingestion.balances import BalanceCheck
from recon.ingestion.format_check import FormatChange
from recon.persistence.models import (
    Alert,
    CounterpartyStat,
    IngestionFile,
    NormalisedTransaction,
    ReconException,
    StatementBalance,
)

_STATS_CHUNK = 1000
_OPEN_STATUSES = ("OPEN", "IN_REVIEW", "ESCALATED", "REOPENED")
_TXN_RECON_PASSES = Decimal("0.95")  # a run this well matched counts as "passing" for C5

StatKey = tuple[str, str, str, str, str]  # bank, source, counterparty, direction, currency


@dataclass(frozen=True, slots=True)
class AlertDraft:
    alert_type: str
    severity: str
    subject: str
    message: str
    details: dict[str, Any]
    dedupe_key: str
    bank_code: str | None = None
    route_to: str = "operations"
    run_id: str | None = None


@dataclass(slots=True)
class ScanOutcome:
    alerts_raised: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    transactions_scanned: int = 0
    already_scanned: bool = False


def _percent(value: Decimal) -> str:
    return f"{value * 100:.2f}%"


class AnomalyService:
    def __init__(
        self,
        session: Session,
        config: AnomalyConfig,
        banks: Mapping[str, BankConfig] | None = None,
    ) -> None:
        self._session = session
        self._config = config
        self._banks = banks or {}

    # -- alert persistence -----------------------------------------------------

    def raise_alerts(self, drafts: list[AlertDraft], now: datetime) -> dict[str, int]:
        """Inserts the drafts that are not already raised; returns new alerts by type."""
        created: dict[str, int] = defaultdict(int)
        for start in range(0, len(drafts), _STATS_CHUNK):
            chunk = drafts[start : start + _STATS_CHUNK]
            rows = self._session.execute(
                insert(Alert)
                .values(
                    [
                        {
                            "alert_type": d.alert_type,
                            "severity": d.severity,
                            "bank_code": d.bank_code,
                            "route_to": d.route_to,
                            "subject": d.subject[:200],
                            "message": d.message,
                            "details": d.details,
                            "dedupe_key": d.dedupe_key[:300],
                            "run_id": d.run_id,
                            "created_at": now,
                        }
                        for d in chunk
                    ]
                )
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
                .returning(Alert.alert_type)
            ).all()
            for (alert_type,) in rows:
                created[alert_type] += 1
        return dict(created)

    # -- ingestion scan --------------------------------------------------------

    def scan_ingestion_file(self, ingestion_file_id: uuid.UUID, now: datetime) -> ScanOutcome:
        ingestion_file = self._session.get(IngestionFile, ingestion_file_id, with_for_update=True)
        if ingestion_file is None:
            raise LookupError(f"no ingestion file {ingestion_file_id}")
        outcome = ScanOutcome()
        if ingestion_file.anomaly_scanned_at is not None:
            outcome.already_scanned = True
            return outcome

        transactions = self._transactions(ingestion_file_id)
        outcome.transactions_scanned = len(transactions)
        groups: dict[StatKey, list[NormalisedTransaction]] = defaultdict(list)
        for txn in transactions:
            groups[self._key(txn)].append(txn)
        baseline = self._load_stats(list(groups))

        drafts = self._amount_alerts(groups, baseline) + self._velocity_alerts(groups, baseline)
        drafts += self._volume_alerts(ingestion_file)
        self._update_stats(groups, baseline, now)
        ingestion_file.anomaly_scanned_at = now
        outcome.by_type = self.raise_alerts(drafts, now)
        outcome.alerts_raised = sum(outcome.by_type.values())
        self._session.flush()
        return outcome

    def _transactions(self, ingestion_file_id: uuid.UUID) -> list[NormalisedTransaction]:
        return list(
            self._session.scalars(
                select(NormalisedTransaction)
                .where(
                    NormalisedTransaction.ingestion_file_id == ingestion_file_id,
                    NormalisedTransaction.counterparty_name_normalised.is_not(None),
                    NormalisedTransaction.counterparty_name_normalised != "",
                )
                .order_by(
                    NormalisedTransaction.txn_date,
                    NormalisedTransaction.txn_timestamp_utc,
                    NormalisedTransaction.id,
                )
            )
        )

    @staticmethod
    def _key(txn: NormalisedTransaction) -> StatKey:
        return (
            txn.bank_code,
            txn.source,
            txn.counterparty_name_normalised or "",
            txn.direction,
            txn.currency,
        )

    def _load_stats(self, keys: list[StatKey]) -> dict[StatKey, RollingStats]:
        stats: dict[StatKey, RollingStats] = {}
        columns = (
            CounterpartyStat.bank_code,
            CounterpartyStat.source,
            CounterpartyStat.counterparty,
            CounterpartyStat.direction,
            CounterpartyStat.currency,
        )
        for start in range(0, len(keys), _STATS_CHUNK):
            for row in self._session.scalars(
                select(CounterpartyStat).where(
                    tuple_(*columns).in_(keys[start : start + _STATS_CHUNK])
                )
            ):
                stats[
                    (row.bank_code, row.source, row.counterparty, row.direction, row.currency)
                ] = RollingStats(
                    row.txn_count, row.mean_minor, row.m2, row.active_days, row.last_active_date
                )
        return stats

    def _amount_alerts(
        self,
        groups: dict[StatKey, list[NormalisedTransaction]],
        baseline: dict[StatKey, RollingStats],
    ) -> list[AlertDraft]:
        cfg = self._config.amount_deviation
        drafts: list[AlertDraft] = []
        for key, txns in groups.items():
            history = baseline.get(key)
            for txn in txns:
                found = amount_deviation(txn.amount_minor, history, cfg)
                if found is None or history is None:
                    continue
                spread = (
                    f"{found.z_score:.2f} standard deviations from the mean"
                    if found.z_score is not None
                    else "different from the only amount ever seen"
                )
                drafts.append(
                    AlertDraft(
                        alert_type="AMOUNT_DEVIATION",
                        severity="CRITICAL" if found.is_whale else "HIGH",
                        subject=f"{key[2]} ({key[3]}, {key[4]})",
                        message=(
                            f"{txn.currency} {txn.amount} for {key[2]} is {spread}"
                            f" and {found.multiple_of_mean:.1f}x the mean of {history.count} "
                            f"earlier transactions."
                        ),
                        details={
                            "normalised_transaction_id": str(txn.id),
                            "reference": txn.normalised_reference,
                            "amount_minor": txn.amount_minor,
                            "mean_minor": str(history.mean.quantize(Decimal("0.01"))),
                            "std_dev_minor": str(history.std_dev.quantize(Decimal("0.01"))),
                            "z_score": (
                                str(found.z_score.quantize(Decimal("0.01")))
                                if found.z_score is not None
                                else None
                            ),
                            "multiple_of_mean": str(
                                found.multiple_of_mean.quantize(Decimal("0.1"))
                            ),
                            "whale": found.is_whale,
                            "history_count": history.count,
                        },
                        dedupe_key=f"amount:{txn.id}",
                        bank_code=txn.bank_code,
                        route_to="compliance",
                    )
                )
        return drafts

    def _velocity_alerts(
        self,
        groups: dict[StatKey, list[NormalisedTransaction]],
        baseline: dict[StatKey, RollingStats],
    ) -> list[AlertDraft]:
        cfg = self._config.velocity
        drafts: list[AlertDraft] = []
        for key, txns in groups.items():
            stamps = [
                t.txn_timestamp_utc for t in txns if not is_date_only(t.txn_timestamp_original)
            ]
            breach = velocity_breach(stamps, baseline.get(key), cfg)
            if breach is None:
                continue
            drafts.append(
                AlertDraft(
                    alert_type="VELOCITY",
                    severity="HIGH",
                    subject=f"{key[2]} ({key[3]}, {key[4]})",
                    message=(
                        f"{breach.count} transactions from {key[2]} within {cfg.window_minutes} "
                        f"minutes against a daily average of {breach.daily_average:.1f}."
                    ),
                    details={
                        "count": breach.count,
                        "window_minutes": cfg.window_minutes,
                        "window_start": breach.window_start.isoformat(),
                        "daily_average": str(breach.daily_average.quantize(Decimal("0.01"))),
                        "multiplier": str(cfg.multiplier),
                    },
                    dedupe_key=f"velocity:{key[0]}:{key[1]}:{key[2]}:{key[3]}:{key[4]}:"
                    f"{breach.window_start.isoformat()}",
                    bank_code=key[0],
                    route_to="compliance",
                )
            )
        return drafts

    def _volume_alerts(self, ingestion_file: IngestionFile) -> list[AlertDraft]:
        cfg = self._config.data_volume
        previous = list(
            self._session.scalars(
                select(IngestionFile.record_count).where(
                    IngestionFile.bank_code == ingestion_file.bank_code,
                    IngestionFile.id != ingestion_file.id,
                    IngestionFile.source.is_not_distinct_from(ingestion_file.source),
                    IngestionFile.status != "REJECTED_DUPLICATE",
                )
            )
        )
        if len(previous) < cfg.min_files:
            return []
        mean = Decimal(sum(previous)) / len(previous)
        if mean <= 0 or Decimal(ingestion_file.record_count) <= cfg.multiplier * mean:
            return []
        return [
            AlertDraft(
                alert_type="DATA_VOLUME",
                severity="HIGH",
                subject=ingestion_file.original_filename,
                message=(
                    f"{ingestion_file.original_filename} holds {ingestion_file.record_count} "
                    f"records, {Decimal(ingestion_file.record_count) / mean:.1f}x the average of "
                    f"{mean:.0f} over {len(previous)} earlier {ingestion_file.bank_code} files."
                ),
                details={
                    "ingestion_file_id": str(ingestion_file.id),
                    "record_count": ingestion_file.record_count,
                    "average_record_count": str(mean.quantize(Decimal("0.1"))),
                    "files_compared": len(previous),
                },
                dedupe_key=f"volume:{ingestion_file.id}",
                bank_code=ingestion_file.bank_code,
                route_to="compliance",
            )
        ]

    def _update_stats(
        self,
        groups: dict[StatKey, list[NormalisedTransaction]],
        baseline: dict[StatKey, RollingStats],
        now: datetime,
    ) -> None:
        # One query for every counterparty this file touched, not one per counterparty (the
        # per-key lookup was a third of ingestion time at scale).
        existing: dict[StatKey, CounterpartyStat] = {}
        banks_sources = {(k[0], k[1]) for k in groups}
        if banks_sources:
            with self._session.no_autoflush:
                for stat in self._session.scalars(
                    select(CounterpartyStat).where(
                        tuple_(CounterpartyStat.bank_code, CounterpartyStat.source).in_(
                            list(banks_sources)
                        ),
                        CounterpartyStat.counterparty.in_({k[2] for k in groups}),
                    )
                ):
                    existing[
                        (
                            stat.bank_code,
                            stat.source,
                            stat.counterparty,
                            stat.direction,
                            stat.currency,
                        )
                    ] = stat
        new_rows: list[dict[str, Any]] = []
        for key, txns in groups.items():
            current = baseline.get(key)
            for txn in txns:  # already ordered by date, so active days count correctly
                current = (
                    observe(current, txn.amount_minor, txn.txn_date)
                    if current
                    else first_observation(txn.amount_minor, txn.txn_date)
                )
            assert current is not None
            row = existing.get(key)
            if row is None:
                new_rows.append(
                    {
                        "bank_code": key[0],
                        "source": key[1],
                        "counterparty": key[2],
                        "direction": key[3],
                        "currency": key[4],
                        "txn_count": current.count,
                        "mean_minor": current.mean,
                        "m2": current.m2,
                        "active_days": current.active_days,
                        "last_active_date": current.last_active_date,
                        "updated_at": now,
                    }
                )
                continue
            row.txn_count = current.count
            row.mean_minor = current.mean
            row.m2 = current.m2
            row.active_days = current.active_days
            row.last_active_date = current.last_active_date
            row.updated_at = now
        if new_rows:  # one multi-row INSERT, not one ORM object per counterparty
            self._session.execute(core_insert(CounterpartyStat), new_rows)

    # -- absence ---------------------------------------------------------------

    def check_absence(self, now: datetime) -> dict[str, int]:
        drafts: list[AlertDraft] = []
        for code, bank in self._banks.items():
            if bank.expected_settlement_time is None:
                continue
            overdue = expected_settlement_overdue(
                now,
                expected_time=bank.expected_settlement_time,
                timezone=bank.timezone,
                calendar=default_calendar(bank.calendar),
                cfg=self._config.absence,
            )
            if overdue is None:
                continue
            day, due = overdue
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
            received = self._session.scalar(
                select(func.count())
                .select_from(IngestionFile)
                .where(
                    IngestionFile.bank_code == code,
                    IngestionFile.source.is_distinct_from("INTERNAL"),
                    IngestionFile.status != "REJECTED_DUPLICATE",
                    IngestionFile.ingested_at >= min(due, day_start),
                )
            )
            if received:
                continue
            drafts.append(
                AlertDraft(
                    alert_type="ABSENCE",
                    severity="HIGH",
                    subject=f"{code} settlement file for {day.isoformat()}",
                    message=(
                        f"No {bank.bank_name} settlement file has arrived for {day.isoformat()}; "
                        f"it was expected by {bank.expected_settlement_time} "
                        f"({bank.timezone}), more than {self._config.absence.grace_minutes} "
                        f"minutes ago."
                    ),
                    details={
                        "expected_by": due.isoformat(),
                        "grace_minutes": self._config.absence.grace_minutes,
                        "business_day": day.isoformat(),
                    },
                    dedupe_key=f"absence:{code}:{day.isoformat()}",
                    bank_code=code,
                )
            )
        return self.raise_alerts(drafts, now)

    # -- duplicate settlements (B4.4) ----------------------------------------------

    def check_settlement_uniqueness(
        self, ingestion_file_id: uuid.UUID, now: datetime
    ) -> dict[str, int]:
        ingestion_file = self._session.get(IngestionFile, ingestion_file_id)
        if ingestion_file is None:
            raise LookupError(f"no ingestion file {ingestion_file_id}")
        if ingestion_file.source == "INTERNAL":
            return {}
        cfg = self._config.settlement_uniqueness
        mine = aliased(NormalisedTransaction)
        others = aliased(NormalisedTransaction)
        refs = (
            select(mine.normalised_reference)
            .where(
                mine.ingestion_file_id == ingestion_file_id,
                mine.normalised_reference.is_not(None),
                mine.normalised_reference != "",
            )
            .distinct()
            .subquery()
        )
        total = self._session.scalar(select(func.count()).select_from(refs)) or 0
        overlap_rows = self._session.execute(
            select(
                others.ingestion_file_id,
                func.count(func.distinct(others.normalised_reference)),
                func.coalesce(func.sum(others.amount_minor).filter(others.currency == "INR"), 0),
            )
            .join(refs, refs.c.normalised_reference == others.normalised_reference)
            .where(
                others.bank_code == ingestion_file.bank_code,
                others.source == "EXTERNAL",
                others.ingestion_file_id != ingestion_file_id,
            )
            .group_by(others.ingestion_file_id)
            .order_by(func.count(func.distinct(others.normalised_reference)).desc())
        ).all()
        if not overlap_rows or not total:
            return {}
        best_file, best_count, best_inr = overlap_rows[0]
        if best_count < cfg.min_overlap_count or Decimal(best_count) / total < cfg.overlap_fraction:
            return {}
        draft = AlertDraft(
            alert_type="DUPLICATE_SETTLEMENT",
            severity="CRITICAL",
            subject=ingestion_file.original_filename,
            message=(
                f"{best_count} of the {total} references in {ingestion_file.original_filename} "
                f"({_percent(Decimal(best_count) / total)}) were already settled in an earlier "
                f"{ingestion_file.bank_code} file; INR {Decimal(best_inr) / 100:,.2f} "
                f"may be paid twice."
            ),
            details={
                "ingestion_file_id": str(ingestion_file_id),
                "earlier_ingestion_file_id": str(best_file),
                "overlapping_references": int(best_count),
                "references_in_file": int(total),
                "overlap_inr_minor": int(best_inr),
                "other_files_overlapping": len(overlap_rows) - 1,
            },
            dedupe_key=f"duplicate-settlement:{ingestion_file_id}",
            bank_code=ingestion_file.bank_code,
            route_to="compliance",
        )
        return self.raise_alerts([draft], now)

    # -- ingestion-time alerts --------------------------------------------------------

    def format_change_alert(
        self,
        *,
        bank_code: str,
        filename: str,
        change: FormatChange,
        blocked: bool,
        now: datetime,
    ) -> dict[str, int]:
        """B4.1: raised the moment a file's header no longer matches the bank's
        configuration, before any record is written."""
        fingerprint = hashlib.sha256("\x1f".join(change.header).encode()).hexdigest()[:16]
        outcome = "blocked" if blocked else "drift"
        draft = AlertDraft(
            alert_type="FORMAT_CHANGE_DETECTED",
            severity="CRITICAL" if blocked else "MEDIUM",
            subject=filename,
            message=(
                f"{bank_code} file {filename} does not match the configured format "
                f"({change.describe()}). "
                + (
                    "The file was rejected; nothing was ingested."
                    if blocked
                    else "It was ingested."
                )
            ),
            details={
                "missing_columns": change.missing,
                "new_columns": change.unexpected,
                "reordered": change.reordered,
                "header": change.header,
                "blocked": blocked,
            },
            dedupe_key=f"format-change:{bank_code}:{fingerprint}:{outcome}",
            bank_code=bank_code,
        )
        return self.raise_alerts([draft], now)

    def pan_exposure_alert(
        self,
        *,
        ingestion_file_id: uuid.UUID,
        bank_code: str,
        filename: str,
        count: int,
        now: datetime,
    ) -> dict[str, int]:
        """C6: a bank sent full card numbers. They were masked before storage; the
        sender needs to be told. No digit of a PAN appears here."""
        draft = AlertDraft(
            alert_type="PAN_EXPOSURE",
            severity="HIGH",
            subject=filename,
            message=(
                f"{count} unmasked card number(s) in {bank_code} file {filename} were masked "
                f"to their last four digits before storage."
            ),
            details={"ingestion_file_id": str(ingestion_file_id), "masked_count": count},
            dedupe_key=f"pan:{ingestion_file_id}",
            bank_code=bank_code,
            route_to="compliance",
        )
        return self.raise_alerts([draft], now)

    def balance_alert(
        self,
        *,
        ingestion_file_id: uuid.UUID,
        bank_code: str,
        filename: str,
        check: BalanceCheck,
        now: datetime,
    ) -> dict[str, int]:
        """C5: the statement's entries do not take its opening balance to its closing balance."""
        if check.balanced:
            return {}
        b = check.balances
        draft = AlertDraft(
            alert_type="BALANCE_MISMATCH",
            severity="CRITICAL",
            subject=filename,
            message=(
                f"{filename}: opening {b.opening_minor} + entries {check.net_entries_minor} = "
                f"{check.computed_closing_minor}, but the statement closes at {b.closing_minor} "
                f"({b.currency} minor units; difference {check.difference_minor})."
                + (
                    f" {check.unparseable_entries} entries could not be read."
                    if check.unparseable_entries
                    else ""
                )
            ),
            details={
                "ingestion_file_id": str(ingestion_file_id),
                "statement_ref": b.statement_ref,
                "currency": b.currency,
                "opening_minor": b.opening_minor,
                "closing_minor": b.closing_minor,
                "computed_closing_minor": check.computed_closing_minor,
                "difference_minor": check.difference_minor,
                "entry_count": check.entry_count,
                "unparseable_entries": check.unparseable_entries,
            },
            dedupe_key=f"balance:{ingestion_file_id}",
            bank_code=bank_code,
            route_to="compliance",
        )
        return self.raise_alerts([draft], now)

    # -- run-level alerts -----------------------------------------------------------

    def evaluate_run(
        self, *, run_id: str, bank_code: str, metrics: dict[str, Any], now: datetime
    ) -> dict[str, int]:
        drafts: list[AlertDraft] = []
        externals = int(metrics.get("external_pool", 0))
        transactions = externals + int(metrics.get("internal_pool", 0))

        rate = Decimal(str(metrics.get("overall_match_rate_of_total", 0)))
        mr = self._config.match_rate
        if externals >= mr.min_externals and rate > mr.threshold:
            drafts.append(
                AlertDraft(
                    alert_type="SUSPICIOUS_MATCH_RATE",
                    severity="HIGH",
                    subject=f"{bank_code} run {run_id}",
                    message=(
                        f"Match rate {_percent(rate)} is above {_percent(mr.threshold)}: "
                        f"duplicate data may be inflating the count (B4.4). Check for a "
                        f"repeated settlement file."
                    ),
                    details={"match_rate": str(rate), "externals": externals},
                    dedupe_key=f"match-rate:{run_id}",
                    bank_code=bank_code,
                    run_id=run_id,
                )
            )

        created = int(metrics.get("exceptions_created", 0))
        er = self._config.exception_rate
        if transactions >= er.min_transactions and transactions > 0:
            exception_rate = Decimal(created) / transactions
            if exception_rate > er.threshold:
                drafts.append(
                    AlertDraft(
                        alert_type="HIGH_EXCEPTION_RATE",
                        severity="HIGH",
                        subject=f"{bank_code} run {run_id}",
                        message=(
                            f"{created} exceptions on {transactions} transactions "
                            f"({_percent(exception_rate)}) exceeds {_percent(er.threshold)}: "
                            f"reconciliation debt is building."
                        ),
                        details={
                            "exceptions_created": created,
                            "transactions": transactions,
                            "exception_rate": str(exception_rate.quantize(Decimal("0.0001"))),
                        },
                        dedupe_key=f"exception-rate:{run_id}",
                        bank_code=bank_code,
                        run_id=run_id,
                    )
                )

        unbalanced = self._session.scalar(
            select(func.count())
            .select_from(StatementBalance)
            .where(StatementBalance.bank_code == bank_code, StatementBalance.balanced.is_(False))
        )
        if unbalanced and rate >= _TXN_RECON_PASSES:
            drafts.append(
                AlertDraft(
                    alert_type="BALANCE_TXN_DIVERGENCE",
                    severity="CRITICAL",
                    subject=f"{bank_code} run {run_id}",
                    message=(
                        f"Transactions reconciled ({_percent(rate)}) but {unbalanced} "
                        f"{bank_code} statement(s) do not balance: a systemic problem (C5)."
                    ),
                    details={"unbalanced_statements": unbalanced, "match_rate": str(rate)},
                    dedupe_key=f"balance-vs-txn:{run_id}",
                    bank_code=bank_code,
                    route_to="compliance",
                    run_id=run_id,
                )
            )

        open_count = (
            self._session.scalar(
                select(func.count())
                .select_from(ReconException)
                .where(ReconException.status.in_(_OPEN_STATUSES))
            )
            or 0
        )
        backlog = self._config.backlog.open_exceptions
        if open_count > backlog:
            drafts.append(
                AlertDraft(
                    alert_type="EXCEPTION_BACKLOG",
                    severity="CRITICAL",
                    subject="open exceptions",
                    message=f"{open_count} open exceptions exceed the backlog limit of {backlog}.",
                    details={"open_exceptions": open_count, "limit": backlog},
                    dedupe_key=f"backlog:{run_id}",
                    run_id=run_id,
                )
            )
        return self.raise_alerts(drafts, now)
