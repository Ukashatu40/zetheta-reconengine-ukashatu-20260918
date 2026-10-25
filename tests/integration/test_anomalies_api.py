"""The anomalies endpoints, and the upload / run integration points."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.api.deps import get_clock, get_db, get_now
from recon.api.main import app
from recon.api.v1.upload import get_upload_dir
from recon.audit.chains import ALERTS_CHAIN
from recon.audit.verifier import verify_chain
from recon.persistence.models import Alert, ApiKey, AuditLog, IngestionFile
from recon.security.api_keys import generate_api_key, hash_api_key
from tests.integration.factories import make_ingestion_file, make_txn

_NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)


@pytest.fixture()
def client(db_session: Session, tmp_path: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_now] = lambda: _NOW
    app.dependency_overrides[get_clock] = lambda: (lambda: _NOW)
    app.dependency_overrides[get_upload_dir] = lambda: tmp_path
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _headers(session: Session, role: str) -> dict[str, str]:
    raw = generate_api_key()
    session.add(ApiKey(key_hash=hash_api_key(raw), name=f"k-{uuid.uuid4().hex[:8]}", role=role))
    session.flush()
    return {"X-API-Key": raw}


def _alert(session: Session, **overrides: Any) -> Alert:
    values: dict[str, Any] = {
        "alert_type": "AMOUNT_DEVIATION",
        "severity": "HIGH",
        "status": "OPEN",
        "bank_code": "HDFC",
        "route_to": "compliance",
        "subject": "ACME",
        "message": "big",
        "details": {},
        "dedupe_key": uuid.uuid4().hex,
    }
    row = Alert(**(values | overrides))
    session.add(row)
    session.flush()
    return row


def test_alerts_can_be_listed_and_filtered(client: TestClient, db_session: Session) -> None:
    viewer = _headers(db_session, "VIEWER")
    _alert(db_session)
    _alert(
        db_session, alert_type="ABSENCE", severity="HIGH", route_to="operations", bank_code="ICICI"
    )
    _alert(db_session, alert_type="VELOCITY", severity="CRITICAL", status="ACKNOWLEDGED")

    def total(query: str) -> int:
        return int(client.get(f"/api/v1/anomalies?{query}", headers=viewer).json()["total"])

    assert total("") == 3
    assert total("alert_type=ABSENCE") == 1
    assert total("severity=CRITICAL") == 1
    assert total("status=OPEN") == 2
    assert total("bank_code=ICICI") == 1
    assert total("route_to=compliance") == 2
    assert client.get("/api/v1/anomalies?severity=URGENT", headers=viewer).status_code == 422


def test_an_analyst_acknowledges_an_alert_once_and_it_is_audited(
    client: TestClient, db_session: Session
) -> None:
    alert = _alert(db_session)
    analyst = _headers(db_session, "ANALYST")
    ok = client.put(
        f"/api/v1/anomalies/{alert.id}/acknowledge", headers=analyst, json={"note": "checked"}
    )
    assert ok.status_code == 200
    assert (ok.json()["status"], ok.json()["acknowledged_by"]) == (
        "ACKNOWLEDGED",
        ok.json()["acknowledged_by"],
    )
    entry = db_session.scalars(select(AuditLog).where(AuditLog.chain_id == ALERTS_CHAIN)).one()
    assert (entry.before_state, entry.after_state, entry.rationale) == (
        {"status": "OPEN"},
        {"status": "ACKNOWLEDGED"},
        "checked",
    )
    assert verify_chain(db_session, ALERTS_CHAIN).ok is True
    assert (
        client.put(
            f"/api/v1/anomalies/{alert.id}/acknowledge", headers=analyst, json={}
        ).status_code
        == 409
    )
    assert (
        client.put(
            f"/api/v1/anomalies/{uuid.uuid4()}/acknowledge", headers=analyst, json={}
        ).status_code
        == 404
    )


def test_a_viewer_cannot_acknowledge_or_scan(client: TestClient, db_session: Session) -> None:
    viewer = _headers(db_session, "VIEWER")
    alert = _alert(db_session)
    assert (
        client.put(f"/api/v1/anomalies/{alert.id}/acknowledge", headers=viewer, json={}).status_code
        == 403
    )
    assert client.post("/api/v1/anomalies/scan", headers=viewer).status_code == 403


def test_the_scan_endpoint_raises_absence_alerts(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    late = datetime(2026, 3, 16, 20, 5, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(UTC)
    app.dependency_overrides[get_now] = lambda: late
    analyst = _headers(db_session, "ANALYST")
    first = client.post("/api/v1/anomalies/scan", headers=analyst).json()
    second = client.post("/api/v1/anomalies/scan", headers=analyst).json()
    assert (first, second) == ({"alerts_raised": 1}, {"alerts_raised": 0})
    assert db_session.query(Alert).filter_by(alert_type="ABSENCE", bank_code="HDFC").count() == 1


def test_the_dashboard_counts_open_alerts(client: TestClient, db_session: Session) -> None:
    _alert(db_session)
    _alert(db_session, status="ACKNOWLEDGED")
    body = client.get("/api/v1/dashboard/summary", headers=_headers(db_session, "VIEWER")).json()
    assert body["open_alert_count"] == 1


def test_an_upload_records_its_side_and_location_and_runs_the_scans(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    csv = (
        "Transaction Reference,Value Date,Amount,Dr/Cr,Counterparty Name,Narration\n"
        "UTR0001,15-03-2026,1000.00,CR,Acme Pvt Ltd,payment\n"
    )
    response = client.post(
        "/api/v1/files/upload",
        headers=_headers(db_session, "ANALYST"),
        data={"bank_code": "HDFC", "source": "EXTERNAL", "format_type": "CSV"},
        files={"file": ("hdfc_settlement.csv", csv, "text/csv")},
    )
    assert response.status_code == 201
    assert response.json()["alerts_raised"] == 0
    row = db_session.get(IngestionFile, uuid.UUID(response.json()["ingestion_file_id"]))
    assert row is not None
    assert row.source == "EXTERNAL"
    assert row.stored_path is not None and Path(row.stored_path).read_text() == csv
    assert row.anomaly_scanned_at == _NOW


def test_a_run_raises_the_suspicious_match_rate_alert(
    client: TestClient, db_session: Session
) -> None:
    f = make_ingestion_file(db_session)
    for i in range(60):  # 60 exact pairs: a 100% match rate on 60 externals
        for source in ("INTERNAL", "EXTERNAL"):
            db_session.add(
                make_txn(
                    f.id, source=source, normalised_reference=f"PAIR{i:03d}", txn_id=f"PAIR{i:03d}"
                )
            )
    db_session.flush()
    response = client.post(
        "/api/v1/reconciliation/run",
        headers={**_headers(db_session, "ANALYST"), "Idempotency-Key": uuid.uuid4().hex},
        json={"bank_code": "HDFC"},
    ).json()
    assert response["metrics"]["alerts_raised"] == {"SUSPICIOUS_MATCH_RATE": 1}
    alert = db_session.query(Alert).filter_by(alert_type="SUSPICIOUS_MATCH_RATE").one()
    assert alert.run_id == response["id"]
