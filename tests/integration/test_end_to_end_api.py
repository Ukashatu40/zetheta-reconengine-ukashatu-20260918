# tests/integration/test_end_to_end_api.py
"""Upload two real CSV files for HDFC, reconcile, and read the results back
through the API. Nothing in the ingestion or matching path is faked."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from recon.api.deps import get_clock, get_db
from recon.api.main import app
from recon.api.v1.upload import get_upload_dir
from recon.config.registry import default_bank_configs
from recon.persistence.models import ApiKey
from recon.security.api_keys import generate_api_key, hash_api_key
from recon.security.rbac import Role

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)  # same day as the data: nothing is stale
_COLUMNS = ("reference", "txn_date", "amount", "direction", "counterparty_name", "narration")
_INTERNAL = [
    ("REF001", "15-03-2026", "1500.00", "CR", "Acme Corp", "Payment received"),
    ("REF002", "15-03-2026", "2500.00", "CR", "Beta Ltd", "Invoice two"),
]
_EXTERNAL = [
    ("REF001", "15-03-2026", "1500.00", "CR", "Acme Corp", "Payment received"),
    ("ZZZ777", "15-03-2026", "900.00", "CR", "Gamma Inc", "Unrelated"),
]


def _fixed_clock() -> Callable[[], datetime]:
    return lambda: _NOW


@pytest.fixture()
def client(db_session: Session, tmp_path: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_clock] = _fixed_clock
    app.dependency_overrides[get_upload_dir] = lambda: tmp_path / "uploads"
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _headers(session: Session, role: Role) -> dict[str, str]:
    raw = generate_api_key()
    session.add(
        ApiKey(key_hash=hash_api_key(raw), name=f"k-{uuid.uuid4().hex[:8]}", role=role.value)
    )
    session.flush()
    return {"X-API-Key": raw}


def _csv(rows: Sequence[tuple[str, ...]]) -> bytes:
    csv_config = default_bank_configs()["HDFC"].csv
    assert csv_config is not None
    header = ",".join(csv_config.column_mapping[column] for column in _COLUMNS)
    return ("\n".join([header, *(",".join(row) for row in rows)]) + "\n").encode()


def _upload(
    client: TestClient, headers: dict[str, str], rows: Sequence[tuple[str, ...]], source: str
) -> Any:
    return client.post(
        "/api/v1/upload",
        headers=headers,
        files={"file": (f"{source.lower()}.csv", _csv(rows), "text/csv")},
        data={"bank_code": "HDFC", "format_type": "CSV", "source": source},
    )


def test_upload_reconcile_and_read_back(client: TestClient, db_session: Session) -> None:
    analyst = _headers(db_session, Role.ANALYST)
    viewer = _headers(db_session, Role.VIEWER)

    internal = _upload(client, analyst, _INTERNAL, "INTERNAL")
    external = _upload(client, analyst, _EXTERNAL, "EXTERNAL")
    for response in (internal, external):
        body = response.json()
        assert response.status_code == 201, body
        assert (body["record_count"], body["parsed_count"], body["error_count"]) == (2, 2, 0)
        assert (body["normalised_count"], body["normalisation_failed_count"]) == (2, 0)

    run = client.post(
        "/api/v1/reconcile",
        headers={**analyst, "Idempotency-Key": "end-to-end-0001"},
        json={"bank_code": "HDFC"},
    )
    metrics = run.json()["metrics"]
    assert (run.status_code, run.json()["status"]) == (201, "COMPLETED")
    assert (metrics["internal_pool"], metrics["external_pool"]) == (2, 2)
    assert metrics["exact_matched"] == 1
    assert metrics["exceptions_by_category"] == {"MISSING_EXTERNAL": 1, "MISSING_INTERNAL": 1}

    results = client.get(f"/api/v1/runs/{run.json()['id']}/results", headers=viewer).json()
    assert [(r["match_type"], r["status"], r["confidence"]) for r in results["items"]] == [
        ("EXACT", "AUTO_MATCHED", "1.000")
    ]

    exceptions = client.get("/api/v1/exceptions", headers=viewer).json()
    assert {e["category"] for e in exceptions["items"]} == {"MISSING_EXTERNAL", "MISSING_INTERNAL"}

    summary = client.get("/api/v1/dashboard/summary", headers=viewer).json()
    assert (summary["total_transactions"], summary["matched_count"]) == (4, 2)

    verification = client.get("/api/v1/audit/verify", headers=viewer).json()
    assert verification["ok"] is True
    assert {"ingestion", "exceptions"} <= set(verification["chains"])


def test_re_uploading_the_same_file_is_rejected(client: TestClient, db_session: Session) -> None:
    analyst = _headers(db_session, Role.ANALYST)
    assert _upload(client, analyst, _INTERNAL, "INTERNAL").status_code == 201
    assert _upload(client, analyst, _INTERNAL, "INTERNAL").status_code == 409
