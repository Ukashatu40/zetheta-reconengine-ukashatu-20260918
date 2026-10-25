"""Chaos at the HTTP boundary: damaged uploads get a clean refusal and leave no trace."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from recon.api.deps import get_clock, get_db
from recon.api.main import app
from recon.api.v1.upload import get_upload_dir
from recon.persistence.models import Alert, ApiKey, IngestionFile
from recon.samples.generator import DEFAULT_SEED, build_sample_set
from recon.security.api_keys import generate_api_key, hash_api_key

pytestmark = pytest.mark.chaos

_NOW = datetime(2026, 3, 13, 12, 0, tzinfo=UTC)
_CSV = next(f for f in build_sample_set(DEFAULT_SEED, 1).files if f.format_type == "CSV")


@pytest.fixture()
def upload_dir(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


@pytest.fixture()
def client(db_session: Session, upload_dir: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_clock] = lambda: lambda: _NOW
    app.dependency_overrides[get_upload_dir] = lambda: upload_dir
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def headers(db_session: Session) -> dict[str, str]:
    raw = generate_api_key()
    db_session.add(
        ApiKey(key_hash=hash_api_key(raw), name=f"k-{uuid.uuid4().hex[:8]}", role="ANALYST")
    )
    db_session.flush()
    return {"X-API-Key": raw}


def _post(client: TestClient, headers: dict[str, str], content: bytes) -> int:
    response = client.post(
        "/api/v1/files/upload",
        headers=headers,
        files={"file": (_CSV.name, content, "text/csv")},
        data={"bank_code": _CSV.bank, "format_type": "CSV", "source": _CSV.source},
    )
    status: int = response.status_code
    return status


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def _leftovers(upload_dir: Path) -> list[Path]:
    return list(upload_dir.iterdir()) if upload_dir.exists() else []


def test_invalid_utf8_is_a_422_and_leaves_no_file_or_row(
    client: TestClient, headers: dict[str, str], db_session: Session, upload_dir: Path
) -> None:
    data = _CSV.content.encode()
    before = _count(db_session, IngestionFile)
    assert _post(client, headers, data[:200] + b"\xff\xfe" + data[200:]) == 422
    assert _count(db_session, IngestionFile) == before
    assert _leftovers(upload_dir) == []


def test_a_renamed_column_is_a_422_with_an_alert_and_no_rows(
    client: TestClient, headers: dict[str, str], db_session: Session, upload_dir: Path
) -> None:
    lines = _CSV.content.splitlines()
    lines[0] = ",".join(lines[0].split(",")[1:])
    before = _count(db_session, IngestionFile)
    assert _post(client, headers, ("\n".join(lines) + "\n").encode()) == 422
    assert _count(db_session, IngestionFile) == before
    assert _leftovers(upload_dir) == []
    types = set(db_session.scalars(select(Alert.alert_type)))
    assert "FORMAT_CHANGE_DETECTED" in types


def test_binary_garbage_is_refused_not_a_500(
    client: TestClient, headers: dict[str, str], upload_dir: Path
) -> None:
    status = _post(client, headers, bytes(range(256)) * 16)
    assert status in {400, 422}
    assert _leftovers(upload_dir) == []


def test_the_same_good_file_twice_is_a_conflict_the_second_time(
    client: TestClient, headers: dict[str, str], upload_dir: Path
) -> None:
    assert _post(client, headers, _CSV.content.encode()) == 201
    assert _post(client, headers, _CSV.content.encode()) == 409
    assert len(_leftovers(upload_dir)) == 1  # only the accepted file is kept
