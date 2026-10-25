"""AnomalyService against the database: the three C2 rules, volume, absence,
duplicate settlements, and the run-level rate alerts."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.anomaly.config import AnomalyConfig, default_anomaly_config
from recon.anomaly.service import AnomalyService
from recon.config.registry import default_bank_configs
from recon.persistence.models import Alert, CounterpartyStat, IngestionFile, ReconException
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)
_PARTY = "ACME PRIVATE LIMITED"


def _service(session: Session, config: AnomalyConfig | None = None) -> AnomalyService:
    return AnomalyService(session, config or default_anomaly_config(), default_bank_configs())


def _file(session: Session, **overrides: Any) -> IngestionFile:
    return make_ingestion_file(session, **({"source": "EXTERNAL"} | overrides))


def _history(
    session: Session, amounts: list[int], *, days: int = 20, **overrides: Any
) -> IngestionFile:
    """A file of earlier transactions for one counterparty, spread over `days` days."""
    f = _file(session, record_count=len(amounts))
    for i, amount in enumerate(amounts):
        _txn(
            session,
            f,
            txn_date=date(2026, 2, 1) + timedelta(days=i * days // len(amounts)),
            amount_minor=amount,
            amount=Decimal(amount) / 100,
            **overrides,
        )
    session.flush()
    return f


def _txn(session: Session, f: IngestionFile, **overrides: Any) -> Any:
    """A transaction in file f, on f's bank and side unless overridden, for _PARTY."""
    values: dict[str, Any] = {
        "source": f.source,
        "bank_code": f.bank_code,
        "counterparty_name_normalised": _PARTY,
        "normalised_reference": uuid.uuid4().hex[:10],
    } | overrides
    txn = make_txn(f.id, **values)
    session.add(txn)
    return txn


def _steady(n: int = 40) -> list[int]:
    return [100_000 + (i % 7) * 100 for i in range(n)]


def _scan(session: Session, f: IngestionFile) -> Any:
    return _service(session).scan_ingestion_file(f.id, _NOW)


def _alerts(session: Session) -> list[Alert]:
    return list(session.scalars(select(Alert).order_by(Alert.created_at, Alert.alert_type)))


# -- amount deviation ----------------------------------------------------------------


def test_the_first_file_builds_the_history_and_raises_nothing(db_session: Session) -> None:
    f = _history(db_session, _steady())
    outcome = _scan(db_session, f)
    assert (outcome.alerts_raised, outcome.transactions_scanned) == (0, 40)
    stat = db_session.scalars(select(CounterpartyStat)).one()
    assert (stat.txn_count, stat.source, stat.direction, stat.currency) == (
        40,
        "EXTERNAL",
        "CR",
        "INR",
    )
    assert f.anomaly_scanned_at == _NOW


def test_an_amount_far_from_the_history_raises_a_compliance_alert(db_session: Session) -> None:
    _scan(db_session, _history(db_session, _steady()))
    later = _file(db_session, record_count=1)
    _txn(db_session, later, txn_date=date(2026, 3, 16), amount_minor=105_000)
    db_session.flush()

    outcome = _scan(db_session, later)

    assert outcome.by_type == {"AMOUNT_DEVIATION": 1}
    alert = _alerts(db_session)[0]
    assert (alert.severity, alert.route_to, alert.status) == ("HIGH", "compliance", "OPEN")
    assert alert.details["history_count"] == 40 and alert.details["whale"] is False
    assert Decimal(alert.details["z_score"]) > 3
    assert alert.bank_code == "HDFC" and "ACME PRIVATE LIMITED" in alert.subject


def test_a_whale_is_critical(db_session: Session) -> None:
    _scan(db_session, _history(db_session, _steady()))
    later = _file(db_session, record_count=1)
    _txn(db_session, later, amount_minor=20_000_000)
    db_session.flush()
    _scan(db_session, later)
    alert = _alerts(db_session)[0]
    assert (alert.severity, alert.details["whale"]) == ("CRITICAL", True)


def test_a_file_is_scanned_once_and_the_statistics_are_not_double_counted(
    db_session: Session,
) -> None:
    f = _history(db_session, _steady())
    _scan(db_session, f)
    again = _scan(db_session, f)
    assert again.already_scanned is True and again.alerts_raised == 0
    assert db_session.scalars(select(CounterpartyStat)).one().txn_count == 40


def test_the_new_file_joins_the_history_after_it_is_judged(db_session: Session) -> None:
    _scan(db_session, _history(db_session, _steady()))
    later = _file(db_session, record_count=1)
    _txn(db_session, later, amount_minor=100_000)
    db_session.flush()
    _scan(db_session, later)
    assert db_session.scalars(select(CounterpartyStat)).one().txn_count == 41


def test_the_history_is_kept_apart_by_source_direction_and_currency(db_session: Session) -> None:
    f = _file(db_session)
    for kwargs in (
        {"source": "EXTERNAL"},
        {"source": "INTERNAL"},
        {"source": "EXTERNAL", "direction": "DR"},
        {"source": "EXTERNAL", "currency": "USD"},
    ):
        db_session.add(
            make_txn(
                f.id,
                counterparty_name_normalised=_PARTY,
                normalised_reference=uuid.uuid4().hex[:8],
                **kwargs,
            )
        )
    db_session.flush()
    _scan(db_session, f)
    assert db_session.query(CounterpartyStat).count() == 4


def test_transactions_without_a_counterparty_are_not_profiled(db_session: Session) -> None:
    f = _file(db_session)
    db_session.add_all(
        [
            make_txn(f.id, counterparty_name_normalised=None),
            make_txn(f.id, counterparty_name_normalised=""),
        ]
    )
    db_session.flush()
    assert _scan(db_session, f).transactions_scanned == 0


def test_an_alert_is_raised_only_once_for_the_same_transaction(db_session: Session) -> None:
    service = _service(db_session)
    _scan(db_session, _history(db_session, _steady()))
    later = _file(db_session, record_count=1)
    txn = make_txn(
        later.id,
        source="EXTERNAL",
        amount_minor=105_000,
        counterparty_name_normalised=_PARTY,
        normalised_reference="ODD1",
    )
    db_session.add(txn)
    db_session.flush()
    service.scan_ingestion_file(later.id, _NOW)
    later.anomaly_scanned_at = None  # force a rescan: the alert must still be unique
    service.scan_ingestion_file(later.id, _NOW)
    assert db_session.query(Alert).filter_by(alert_type="AMOUNT_DEVIATION").count() == 1


# -- velocity --------------------------------------------------------------------------


def test_a_burst_over_the_daily_average_raises_a_velocity_alert(db_session: Session) -> None:
    _scan(db_session, _history(db_session, _steady(20), days=10))  # about 2 a day over 10 days
    burst = _file(db_session, record_count=6)
    base = datetime(2026, 3, 16, 9, 0, tzinfo=UTC)
    for i in range(6):
        stamp = base + timedelta(minutes=5 * i)
        _txn(
            db_session,
            burst,
            txn_timestamp_utc=stamp,
            txn_timestamp_original=stamp.isoformat(),
        )
    db_session.flush()
    outcome = _scan(db_session, burst)
    assert outcome.by_type == {"VELOCITY": 1}
    alert = _alerts(db_session)[0]
    assert (alert.severity, alert.route_to) == ("HIGH", "compliance")
    assert alert.details["count"] == 6 and alert.details["window_minutes"] == 60


def test_date_only_sources_cannot_trip_the_hourly_rule(db_session: Session) -> None:
    _scan(db_session, _history(db_session, _steady(20), days=10))
    burst = _file(db_session, record_count=6)
    for _ in range(6):  # six on one date, no clock time: not an "hourly" burst
        _txn(db_session, burst, txn_timestamp_original="16-03-2026")
    db_session.flush()
    assert _scan(db_session, burst).by_type == {}


# -- data volume -------------------------------------------------------------------------


def test_a_file_far_larger_than_the_banks_norm_raises_a_volume_alert(db_session: Session) -> None:
    for _ in range(6):
        _file(db_session, record_count=100)
    big = _file(db_session, record_count=1_000)
    outcome = _scan(db_session, big)
    assert outcome.by_type == {"DATA_VOLUME": 1}
    alert = _alerts(db_session)[0]
    assert alert.details["record_count"] == 1_000 and alert.details["files_compared"] == 6


def test_a_normal_sized_file_or_too_few_earlier_files_raise_nothing(db_session: Session) -> None:
    for _ in range(6):
        _file(db_session, record_count=100)
    assert _scan(db_session, _file(db_session, record_count=250)).by_type == {}
    fresh = _file(db_session, bank_code="ICICI", record_count=5_000)  # no earlier ICICI files
    assert _scan(db_session, fresh).by_type == {}


# -- absence -----------------------------------------------------------------------------


def _ist(hour: int, minute: int = 0, day: date = date(2026, 3, 16)) -> datetime:
    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo("Asia/Kolkata")
    ).astimezone(UTC)


def test_a_bank_with_no_file_two_hours_past_its_expected_time_raises_an_absence_alert(
    db_session: Session,
) -> None:
    raised = _service(db_session).check_absence(_ist(20, 5))
    assert raised == {
        "ABSENCE": 1
    }  # only HDFC (due 18:00) is past its grace; others are not due yet
    alert = db_session.scalars(select(Alert).where(Alert.bank_code == "HDFC")).one()
    assert (alert.severity, alert.route_to) == ("HIGH", "operations")
    assert alert.details["business_day"] == "2026-03-16"
    assert alert.dedupe_key == "absence:HDFC:2026-03-16"


def test_a_file_that_arrived_today_clears_the_absence(db_session: Session) -> None:
    _file(db_session, bank_code="HDFC", ingested_at=_ist(18, 30))
    _service(db_session).check_absence(_ist(20, 5))
    assert db_session.query(Alert).filter_by(bank_code="HDFC").count() == 0


def test_an_internal_ledger_file_does_not_count_as_the_banks_settlement(
    db_session: Session,
) -> None:
    _file(db_session, bank_code="HDFC", source="INTERNAL", ingested_at=_ist(18, 30))
    _service(db_session).check_absence(_ist(20, 5))
    assert db_session.query(Alert).filter_by(bank_code="HDFC").count() == 1


def test_no_absence_inside_the_grace_period_or_on_a_weekend(db_session: Session) -> None:
    service = _service(db_session)
    assert service.check_absence(_ist(19, 30)) == {}
    assert service.check_absence(_ist(22, 0, date(2026, 3, 15))) == {}  # a Sunday


def test_the_same_absence_is_not_raised_twice(db_session: Session) -> None:
    service = _service(db_session)
    service.check_absence(_ist(20, 5))
    service.check_absence(_ist(21, 5))  # ICICI and AXIS are now overdue too; HDFC is not repeated
    assert db_session.query(Alert).filter_by(bank_code="HDFC").count() == 1
    assert db_session.query(Alert).filter_by(alert_type="ABSENCE").count() == 3


# -- duplicate settlements (B4.4) --------------------------------------------------------


def _settlement_file(session: Session, refs: list[str], **overrides: Any) -> IngestionFile:
    f = _file(session, record_count=len(refs), **overrides)
    for ref in refs:
        _txn(
            session,
            f,
            normalised_reference=ref,
            amount_minor=470_000_000 // max(len(refs), 1),
            counterparty_name_normalised=None,
        )
    session.flush()
    return f


def test_the_same_settlement_arriving_in_a_second_file_is_a_critical_alert(
    db_session: Session,
) -> None:
    refs = [f"UTR{i:05d}" for i in range(40)]
    _settlement_file(db_session, refs, original_filename="hdfc_mt940_0315.sta")
    second = _settlement_file(db_session, refs, original_filename="hdfc_mt940_0315_copy.sta")

    raised = _service(db_session).check_settlement_uniqueness(second.id, _NOW)

    assert raised == {"DUPLICATE_SETTLEMENT": 1}
    alert = _alerts(db_session)[0]
    assert (alert.severity, alert.route_to) == ("CRITICAL", "compliance")
    assert (
        alert.details["overlapping_references"] == 40 and alert.details["references_in_file"] == 40
    )
    assert alert.details["overlap_inr_minor"] == 470_000_000
    assert "may be paid twice" in alert.message


def test_a_small_or_partial_overlap_is_not_a_duplicate_settlement(db_session: Session) -> None:
    refs = [f"UTR{i:05d}" for i in range(40)]
    _settlement_file(db_session, refs)
    partial = _settlement_file(db_session, refs[:5] + [f"NEW{i:05d}" for i in range(35)])
    assert _service(db_session).check_settlement_uniqueness(partial.id, _NOW) == {}  # 5 < min 10
    mostly_new = _settlement_file(db_session, refs[:12] + [f"FRESH{i:05d}" for i in range(48)])
    assert _service(db_session).check_settlement_uniqueness(mostly_new.id, _NOW) == {}  # 20% < 50%


def test_other_banks_and_the_internal_ledger_do_not_count(db_session: Session) -> None:
    refs = [f"UTR{i:05d}" for i in range(40)]
    _settlement_file(db_session, refs, bank_code="ICICI")
    same_refs_other_bank = _settlement_file(db_session, refs)
    assert _service(db_session).check_settlement_uniqueness(same_refs_other_bank.id, _NOW) == {}
    ledger = _settlement_file(db_session, refs, source="INTERNAL")
    assert _service(db_session).check_settlement_uniqueness(ledger.id, _NOW) == {}


# -- run-level alerts --------------------------------------------------------------------


def _metrics(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "external_pool": 1_000,
        "internal_pool": 1_000,
        "overall_match_rate_of_total": 0.90,
        "exceptions_created": 10,
    }
    return values | overrides


def _evaluate(session: Session, metrics: dict[str, Any], run_id: str = "run-x") -> dict[str, int]:
    return _service(session).evaluate_run(
        run_id=run_id, bank_code="HDFC", metrics=metrics, now=_NOW
    )


def test_a_match_rate_above_99_5_percent_is_suspicious(db_session: Session) -> None:
    assert _evaluate(db_session, _metrics(overall_match_rate_of_total=0.998)) == {
        "SUSPICIOUS_MATCH_RATE": 1
    }
    alert = _alerts(db_session)[0]
    assert "duplicate data" in alert.message and alert.run_id == "run-x"


def test_exactly_99_5_percent_or_a_small_run_is_not(db_session: Session) -> None:
    assert _evaluate(db_session, _metrics(overall_match_rate_of_total=0.995)) == {}
    assert (
        _evaluate(
            db_session,
            _metrics(external_pool=20, internal_pool=20, overall_match_rate_of_total=1.0),
            "r2",
        )
        == {}
    )


def test_an_exception_rate_above_5_percent_is_an_alert(db_session: Session) -> None:
    assert _evaluate(db_session, _metrics(exceptions_created=101)) == {
        "HIGH_EXCEPTION_RATE": 1
    }  # 101/2000
    assert _evaluate(db_session, _metrics(exceptions_created=100), "r2") == {}  # exactly 5%


def _open_exception() -> ReconException:
    return ReconException(
        dedupe_key=uuid.uuid4().hex,
        category="MISSING_INTERNAL",
        severity="HIGH",
        status="OPEN",
        assigned_tier=2,
        bank_code="HDFC",
        affected_records={},
        suggested_resolution="x",
        rationale="y",
        sla_deadline=_NOW,
    )


def test_a_backlog_over_the_limit_is_critical(db_session: Session) -> None:
    config = default_anomaly_config()
    config = config.model_copy(
        update={"backlog": config.backlog.model_copy(update={"open_exceptions": 2})}
    )
    for _ in range(3):
        db_session.add(_open_exception())
    db_session.flush()
    assert _service(db_session, config).evaluate_run(
        run_id="r", bank_code="HDFC", metrics=_metrics(), now=_NOW
    ) == {"EXCEPTION_BACKLOG": 1}
    assert _alerts(db_session)[0].severity == "CRITICAL"


def test_the_thresholds_come_from_the_configuration(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = default_anomaly_config()
    tight = config.model_copy(
        update={
            "exception_rate": config.exception_rate.model_copy(
                update={"threshold": Decimal("0.001")}
            )
        }
    )
    raised = _service(db_session, tight).evaluate_run(
        run_id="r", bank_code="HDFC", metrics=_metrics(exceptions_created=10), now=_NOW
    )
    assert raised == {"HIGH_EXCEPTION_RATE": 1}
