"""Dashboard endpoints: summary trend, per-bank breakdown, hourly timeline, caching."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from recon import cache
from recon.api.deps import get_db, get_now
from recon.api.main import app
from recon.persistence.models import ApiKey, ReconciliationRun, ReconException
from recon.security.api_keys import generate_api_key, hash_api_key
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_now] = lambda: _NOW
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def viewer(db_session: Session) -> dict[str, str]:
    raw = generate_api_key()
    db_session.add(
        ApiKey(key_hash=hash_api_key(raw), name=f"v-{uuid.uuid4().hex[:6]}", role="VIEWER")
    )
    db_session.flush()
    return {"X-API-Key": raw}


def _exc(session: Session, **overrides: Any) -> ReconException:
    values: dict[str, Any] = {
        "dedupe_key": uuid.uuid4().hex,
        "category": "MISSING_INTERNAL",
        "severity": "HIGH",
        "status": "OPEN",
        "assigned_tier": 2,
        "bank_code": "HDFC",
        "affected_records": {},
        "suggested_resolution": "x",
        "rationale": "y",
        "created_at": _NOW,
        "sla_deadline": _NOW + timedelta(hours=4),
    }
    values.update(overrides)
    row = ReconException(**values)
    session.add(row)
    session.flush()
    return row


def test_summary_has_a_seven_day_trend_ending_at_as_of(
    client: TestClient, db_session: Session, viewer: dict[str, str]
) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all(
        [
            make_txn(f.id, txn_date=date(2026, 3, 15), match_status="MATCHED"),
            make_txn(f.id, txn_date=date(2026, 3, 15), match_status="UNMATCHED"),
            make_txn(f.id, txn_date=date(2026, 3, 13), match_status="MATCHED"),
            make_txn(f.id, txn_date=date(2026, 3, 1), match_status="MATCHED"),  # outside the window
        ]
    )
    _exc(db_session, created_at=_NOW)
    db_session.flush()

    body = client.get("/api/v1/dashboard/summary", headers=viewer).json()

    trend = body["daily_trend"]
    assert [p["day"] for p in trend] == [f"2026-03-{d:02d}" for d in range(9, 16)]
    last = trend[-1]
    assert (last["total_transactions"], last["matched_count"], last["match_rate"]) == (2, 1, 0.5)
    assert last["exception_count"] == 1
    assert trend[4]["total_transactions"] == 1  # 13 March
    assert body["match_rate"] == body["match_rate_of_total"] == 0.75


def test_summary_reports_the_average_run_duration(
    client: TestClient, db_session: Session, viewer: dict[str, str]
) -> None:
    for seconds in (10, 20):
        db_session.add(
            ReconciliationRun(
                idempotency_key=uuid.uuid4().hex,
                bank_code="HDFC",
                status="COMPLETED",
                requested_by="t",
                started_at=_NOW,
                completed_at=_NOW + timedelta(seconds=seconds),
            )
        )
    db_session.flush()
    body = client.get("/api/v1/dashboard/summary", headers=viewer).json()
    assert body["avg_processing_time_seconds"] == 15.0


def test_summary_average_is_null_with_no_completed_runs(
    client: TestClient, viewer: dict[str, str]
) -> None:
    assert (
        client.get("/api/v1/dashboard/summary", headers=viewer).json()[
            "avg_processing_time_seconds"
        ]
        is None
    )


def test_banks_breakdown_has_rates_settlement_time_and_sla_compliance(
    client: TestClient, db_session: Session, viewer: dict[str, str]
) -> None:
    f = make_ingestion_file(db_session)
    db_session.add_all(
        [
            make_txn(
                f.id, source="EXTERNAL", match_status="MATCHED", settlement_date=date(2026, 3, 16)
            ),
            make_txn(
                f.id, source="EXTERNAL", match_status="MATCHED", settlement_date=date(2026, 3, 17)
            ),
            make_txn(f.id, source="INTERNAL", match_status="UNMATCHED"),
            make_txn(f.id, source="INTERNAL", match_status="UNMATCHED"),
        ]
    )
    _exc(db_session)  # open, inside its SLA: compliant
    _exc(db_session, status="ESCALATED", sla_breached_at=_NOW)  # breached
    _exc(db_session, status="RESOLVED", resolved_at=_NOW + timedelta(hours=1))  # on time
    _exc(db_session, status="RESOLVED", resolved_at=_NOW + timedelta(hours=9))  # late
    db_session.flush()

    banks = {
        b["bank_code"]: b
        for b in client.get("/api/v1/dashboard/banks", headers=viewer).json()["banks"]
    }

    hdfc = banks["HDFC"]
    assert hdfc["bank_name"] == "HDFC Bank"
    assert (hdfc["total_transactions"], hdfc["matched_count"], hdfc["match_rate"]) == (4, 2, 0.5)
    assert (hdfc["exception_count"], hdfc["open_exception_count"]) == (4, 2)
    assert hdfc["exception_rate"] == 1.0
    assert hdfc["avg_settlement_days"] == 1.5
    assert hdfc["sla_compliance_percent"] == 50.0
    assert banks["ICICI"]["sla_compliance_percent"] == 100.0  # configured, no exceptions
    assert banks["ICICI"]["avg_settlement_days"] is None


def test_timeline_has_24_hours_with_volume_and_match_rate(
    client: TestClient, db_session: Session, viewer: dict[str, str]
) -> None:
    f = make_ingestion_file(db_session)

    def at(hour: int) -> datetime:
        return datetime(2026, 3, 15, hour, 30, tzinfo=UTC)

    db_session.add_all(
        [
            make_txn(f.id, txn_timestamp_utc=at(9), match_status="MATCHED"),
            make_txn(f.id, txn_timestamp_utc=at(9), match_status="UNMATCHED"),
            make_txn(f.id, txn_timestamp_utc=at(17), match_status="MATCHED"),
            make_txn(f.id, txn_timestamp_utc=datetime(2026, 3, 14, 9, tzinfo=UTC)),  # other day
        ]
    )
    db_session.flush()

    body = client.get("/api/v1/dashboard/timeline", headers=viewer).json()

    assert (body["date"], body["total_transactions"], len(body["hours"])) == ("2026-03-15", 3, 24)
    by_hour = {h["hour"]: h for h in body["hours"]}
    assert (by_hour[9]["transaction_count"], by_hour[9]["match_rate"]) == (2, 0.5)
    assert (by_hour[17]["transaction_count"], by_hour[17]["match_rate"]) == (1, 1.0)
    assert by_hour[0] == {"hour": 0, "transaction_count": 0, "matched_count": 0, "match_rate": 0.0}


def test_timeline_accepts_an_explicit_date(
    client: TestClient, db_session: Session, viewer: dict[str, str]
) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(make_txn(f.id, txn_timestamp_utc=datetime(2026, 3, 14, 9, tzinfo=UTC)))
    db_session.flush()
    body = client.get("/api/v1/dashboard/timeline?date=2026-03-14", headers=viewer).json()
    assert body["total_transactions"] == 1


def test_dashboard_responses_are_cached_for_the_ttl(
    client: TestClient, db_session: Session, viewer: dict[str, str]
) -> None:
    f = make_ingestion_file(db_session)
    db_session.add(make_txn(f.id))
    db_session.flush()
    first = client.get("/api/v1/dashboard/summary", headers=viewer).json()["total_transactions"]

    db_session.add(make_txn(f.id))
    db_session.flush()
    cached = client.get("/api/v1/dashboard/summary", headers=viewer).json()["total_transactions"]

    cache.get_cache.cache_clear()
    fresh = client.get("/api/v1/dashboard/summary", headers=viewer).json()["total_transactions"]
    assert (first, cached, fresh) == (1, 1, 2)


def test_a_corrupt_cache_entry_is_treated_as_a_miss(
    client: TestClient, db_session: Session, viewer: dict[str, str]
) -> None:
    cache.get_cache().set("dashboard:banks", "{not json", 30)
    response = client.get("/api/v1/dashboard/banks", headers=viewer)
    assert response.status_code == 200
    assert {b["bank_code"] for b in response.json()["banks"]} >= {"HDFC", "ICICI"}
