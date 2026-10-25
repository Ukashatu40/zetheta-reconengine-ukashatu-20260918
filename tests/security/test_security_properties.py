"""Security properties checked end to end against a real database (A9.2, A9.3, C6)."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from recon.api.deps import get_clock, get_db
from recon.api.main import app
from recon.api.v1.upload import get_upload_dir
from recon.ingestion.pipeline import IngestionPipeline
from recon.persistence.models import ApiKey
from recon.runs.settings import default_settings
from recon.samples.generator import DEFAULT_SEED, build_sample_set
from recon.security.api_keys import generate_api_key, hash_api_key

pytestmark = pytest.mark.security

_NOW = datetime(2026, 3, 13, 12, 0, tzinfo=UTC)
_CARD = "4111111111111111"
_CARD_SPACED = "4111 1111 1111 1111"


@pytest.fixture()
def client(db_session: Session, tmp_path: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_clock] = lambda: lambda: _NOW
    app.dependency_overrides[get_upload_dir] = lambda: tmp_path / "uploads"
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _key(session: Session, role: str) -> dict[str, str]:
    raw = generate_api_key()
    session.add(ApiKey(key_hash=hash_api_key(raw), name=f"k-{uuid.uuid4().hex[:8]}", role=role))
    session.flush()
    return {"X-API-Key": raw}


def test_no_key_and_a_bad_key_are_401(client: TestClient) -> None:
    assert client.get("/api/v1/exceptions").status_code == 401
    assert client.get("/api/v1/exceptions", headers={"X-API-Key": "rk_nope"}).status_code == 401


@pytest.mark.parametrize(
    ("role", "method", "path", "expected"),
    [
        ("VIEWER", "GET", "/api/v1/exceptions", 200),
        ("VIEWER", "POST", "/api/v1/reconciliation/run", 403),
        ("VIEWER", "POST", "/api/v1/files/upload", 403),
        ("VIEWER", "POST", "/api/v1/admin/users", 403),
        ("ANALYST", "POST", "/api/v1/admin/users", 403),
        ("ANALYST", "POST", "/api/v1/admin/banks/reload", 403),
        ("ANALYST", "GET", "/api/v1/admin/users", 403),
        ("ADMIN", "GET", "/api/v1/admin/users", 200),
    ],
)
def test_the_role_matrix_is_enforced(
    client: TestClient, db_session: Session, *, role: str, method: str, path: str, expected: int
) -> None:
    response = client.request(method, path, headers=_key(db_session, role))
    assert response.status_code == expected


def test_an_issued_key_is_shown_once_and_only_its_hash_is_stored_or_audited(
    client: TestClient, db_session: Session
) -> None:
    admin = _key(db_session, "ADMIN")
    user = client.post(
        "/api/v1/admin/users", headers=admin, json={"username": "casey", "role": "ANALYST"}
    )
    assert user.status_code in {200, 201}, user.text
    issued = client.post(
        f"/api/v1/admin/users/{user.json()['id']}/api-keys", headers=admin, json={}
    )
    assert issued.status_code in {200, 201}, issued.text
    raw = issued.json()["api_key"]
    assert client.get("/api/v1/exceptions", headers={"X-API-Key": raw}).status_code == 200

    stored = db_session.execute(text("select key_hash from api_keys")).scalars().all()
    assert hashlib.sha256(raw.encode()).hexdigest() in stored and raw not in stored
    for table in ("api_keys", "audit.audit_log", "users"):
        hits = db_session.execute(
            text(f"select count(*) from {table} t where t::text like :needle"),
            {"needle": f"%{raw}%"},
        ).scalar_one()
        assert hits == 0, f"the raw key appears in {table}"


def test_a_deactivated_users_key_stops_working(client: TestClient, db_session: Session) -> None:
    admin = _key(db_session, "ADMIN")
    user = client.post(
        "/api/v1/admin/users", headers=admin, json={"username": "dana", "role": "VIEWER"}
    ).json()
    raw = client.post(f"/api/v1/admin/users/{user['id']}/api-keys", headers=admin, json={}).json()[
        "api_key"
    ]
    assert client.get("/api/v1/exceptions", headers={"X-API-Key": raw}).status_code == 200
    assert client.post(
        f"/api/v1/admin/users/{user['id']}/deactivate", headers=admin
    ).status_code in {
        200,
        204,
    }
    assert client.get("/api/v1/exceptions", headers={"X-API-Key": raw}).status_code == 401


def test_a_full_card_number_is_never_persisted_or_audited(
    db_session: Session, tmp_path: Path
) -> None:
    sample = build_sample_set(DEFAULT_SEED, 1)
    f = next(f for f in sample.files if f.format_type == "CSV" and f.source == "EXTERNAL")
    marker = next(line.split(",")[0] for line in f.content.splitlines()[1:2])
    content = f.content.replace("Credit received", f"Card {_CARD_SPACED} refund {_CARD}", 1)
    assert content != f.content, "the sample narration changed; update this test"
    path = tmp_path / "card.csv"
    path.write_text(content, encoding="utf-8")
    settings = default_settings()
    outcome = IngestionPipeline(session=db_session, settings=settings, actor="sec").process(
        path=path,
        display_name="card.csv",
        sha256=hashlib.sha256(content.encode()).hexdigest(),
        size_bytes=len(content),
        config=settings.banks[f.bank],
        format_type="CSV",
        source="EXTERNAL",
        now=_NOW,
    )
    assert outcome.result.pan_masked_count >= 1, marker
    for table in (
        "raw_transactions",
        "normalised_transactions",
        "ingestion_files",
        "audit.audit_log",
        "alerts",
        "exceptions",
    ):
        for needle in (_CARD, _CARD_SPACED):
            hits = db_session.execute(
                text(f"select count(*) from {table} t where t::text like :n"),
                {"n": f"%{needle}%"},
            ).scalar_one()
            assert hits == 0, f"full card number found in {table}"
    masked = db_session.execute(
        text("select count(*) from raw_transactions t where t::text like '%************1111%'")
    ).scalar_one()
    assert masked >= 1
