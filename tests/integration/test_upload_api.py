# tests/integration/test_upload_api.py
"""Upload endpoint tests. The three ingestion services are replaced by fakes
for the orchestration tests: no real bank file has been parsed here yet."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.api.deps import get_clock, get_db
from recon.api.main import app
from recon.api.v1.upload import get_bank_configs, get_upload_dir
from recon.audit.chains import INGESTION_CHAIN
from recon.config.registry import default_bank_configs
from recon.ingestion.normalisation_service import NormalisationResult
from recon.ingestion.service import IngestionResult
from recon.persistence.models import ApiKey, AuditLog, IngestionFile
from recon.persistence.repositories.ingestion import DuplicateFileError
from recon.security.api_keys import generate_api_key, hash_api_key
from recon.security.rbac import Role
from tests.integration.factories import make_ingestion_file

_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
_CONTENT = b"date,amount\n2026-03-15,100.00\n"


def _fixed_clock() -> Callable[[], datetime]:
    return lambda: _NOW


@pytest.fixture()
def upload_dir(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


@pytest.fixture()
def client(db_session: Session, upload_dir: Path) -> Iterator[TestClient]:
    hdfc = default_bank_configs()["HDFC"].model_copy(update={"supported_formats": ["CSV"]})
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_clock] = _fixed_clock
    app.dependency_overrides[get_upload_dir] = lambda: upload_dir
    app.dependency_overrides[get_bank_configs] = lambda: {"HDFC": hdfc}
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


def _post(
    client: TestClient,
    headers: dict[str, str],
    *,
    content: bytes = _CONTENT,
    filename: str = "../../stmt.csv",
    bank: str = "HDFC",
    fmt: str = "CSV",
    source: str = "EXTERNAL",
) -> Any:
    return client.post(
        "/api/v1/upload",
        headers=headers,
        files={"file": (filename, content, "text/csv")},
        data={"bank_code": bank, "format_type": fmt, "source": source},
    )


def _files(upload_dir: Path) -> list[Path]:
    return list(upload_dir.iterdir()) if upload_dir.exists() else []


class _FakeIngestion:
    def __init__(self, session: Session, ingested_by: str) -> None:
        self._session = session

    def ingest_file(self, path: Path, config: object, format_type: str) -> IngestionResult:
        row = make_ingestion_file(
            self._session,
            original_filename=path.name,
            content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size_bytes=path.stat().st_size,
        )
        return IngestionResult(
            str(row.id), record_count=3, parsed_count=2, error_count=1, status="COMPLETED"
        )


class _FakeNormalisation:
    def __init__(self, session: Session) -> None:
        pass

    def normalise_ingestion_file(self, *args: object, **kwargs: object) -> NormalisationResult:
        return NormalisationResult(normalised_count=2, failed_count=0, failures=[])


class _ExplodingIngestion(_FakeIngestion):
    def ingest_file(self, path: Path, config: object, format_type: str) -> IngestionResult:
        super().ingest_file(path, config, format_type)  # writes a row that must be rolled back
        raise ValueError("bad file: secret detail")


def test_a_viewer_cannot_upload(client: TestClient, db_session: Session, upload_dir: Path) -> None:
    assert _post(client, _headers(db_session, Role.VIEWER)).status_code == 403
    assert _files(upload_dir) == []


def test_unknown_bank_is_422(client: TestClient, db_session: Session) -> None:
    assert _post(client, _headers(db_session, Role.ANALYST), bank="NOPE").status_code == 422


def test_a_format_the_bank_does_not_support_is_422(client: TestClient, db_session: Session) -> None:
    response = _post(client, _headers(db_session, Role.ANALYST), fmt="MT940", filename="s.sta")
    assert response.status_code == 422
    assert "does not support" in response.json()["detail"]


def test_a_wrong_extension_is_422_and_nothing_is_saved(
    client: TestClient, db_session: Session, upload_dir: Path
) -> None:
    response = _post(client, _headers(db_session, Role.ANALYST), filename="stmt.exe")
    assert response.status_code == 422
    assert _files(upload_dir) == []


@pytest.mark.parametrize(
    ("field", "value"), [("source", "BOTH"), ("format_type", "PDF"), ("bank_code", "hdfc")]
)
def test_invalid_form_values_are_422(
    client: TestClient, db_session: Session, field: str, value: str
) -> None:
    data = {"bank_code": "HDFC", "format_type": "CSV", "source": "EXTERNAL", field: value}
    response = client.post(
        "/api/v1/upload",
        headers=_headers(db_session, Role.ANALYST),
        files={"file": ("s.csv", _CONTENT, "text/csv")},
        data=data,
    )
    assert response.status_code == 422


def test_an_empty_file_is_422(client: TestClient, db_session: Session, upload_dir: Path) -> None:
    assert _post(client, _headers(db_session, Role.ANALYST), content=b"").status_code == 422
    assert _files(upload_dir) == []


def test_an_oversized_file_is_413_and_nothing_is_saved(
    client: TestClient, db_session: Session, upload_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("recon.api.v1.upload.MAX_UPLOAD_BYTES", 10)
    response = _post(client, _headers(db_session, Role.ANALYST), content=b"x" * 11)
    assert response.status_code == 413
    assert _files(upload_dir) == []


def test_duplicate_content_is_409_before_any_parsing(
    client: TestClient, db_session: Session, upload_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_ingestion_file(db_session, content_sha256=hashlib.sha256(_CONTENT).hexdigest())
    monkeypatch.setattr(
        "recon.api.v1.upload.IngestionService", _ExplodingIngestion
    )  # must not be reached
    response = _post(client, _headers(db_session, Role.ANALYST))
    assert response.status_code == 409
    assert _files(upload_dir) == []


def test_a_successful_upload_is_stored_audited_and_reported(
    client: TestClient, db_session: Session, upload_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("recon.api.v1.upload.IngestionService", _FakeIngestion)
    monkeypatch.setattr("recon.api.v1.upload.NormalisationService", _FakeNormalisation)

    response = _post(client, _headers(db_session, Role.ANALYST))

    body = response.json()
    assert response.status_code == 201
    assert body["original_filename"] == "stmt.csv"  # traversal stripped
    assert body["content_sha256"] == hashlib.sha256(_CONTENT).hexdigest()
    assert (body["record_count"], body["parsed_count"], body["normalised_count"]) == (3, 2, 2)
    assert body["exceptions_created"] == 0

    stored = _files(upload_dir)
    assert len(stored) == 1
    assert stored[0].name != "stmt.csv" and stored[0].read_bytes() == _CONTENT
    row = db_session.get(IngestionFile, uuid.UUID(body["ingestion_file_id"]))
    assert row is not None and row.original_filename == "stmt.csv"
    entry = db_session.scalars(select(AuditLog).where(AuditLog.chain_id == INGESTION_CHAIN)).one()
    assert entry.action_type == "INGEST"
    assert entry.after_state is not None and entry.after_state["size_bytes"] == len(_CONTENT)


def test_a_processing_failure_is_422_and_rolls_back_and_deletes_the_file(
    client: TestClient, db_session: Session, upload_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("recon.api.v1.upload.IngestionService", _ExplodingIngestion)

    response = _post(client, _headers(db_session, Role.ANALYST))

    assert response.status_code == 422
    assert "secret" not in response.text
    assert _files(upload_dir) == []
    assert db_session.query(IngestionFile).count() == 0


class _DuplicateRaisingIngestion:
    def __init__(self, session: Session, ingested_by: str) -> None:
        pass

    def ingest_file(self, path: Path, config: object, format_type: str) -> IngestionResult:
        raise DuplicateFileError("a" * 64, uuid.uuid4())


def test_a_duplicate_detected_during_ingestion_is_409_not_422(
    client: TestClient, db_session: Session, upload_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race the hash pre-check cannot close: another upload wins between
    the check and the insert."""
    monkeypatch.setattr("recon.api.v1.upload.IngestionService", _DuplicateRaisingIngestion)
    response = _post(client, _headers(db_session, Role.ANALYST))
    assert response.status_code == 409
    assert _files(upload_dir) == []
