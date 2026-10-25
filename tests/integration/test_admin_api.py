"""Administration: bank configuration (hot-swap, versions, validation) and users."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.api.deps import get_db, get_now
from recon.api.main import app
from recon.audit.chains import CONFIG_CHAIN, USERS_CHAIN
from recon.audit.verifier import verify_chain
from recon.config.registry import BankConfigRegistry
from recon.config.store import sync_bank_configurations
from recon.paths import config_dir
from recon.persistence.models import ApiKey, AuditLog, BankConfiguration, User
from recon.security.api_keys import generate_api_key, hash_api_key

_NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)
_ICICI_YAML = (config_dir() / "banks" / "icici.v1.yaml").read_text()


@pytest.fixture()
def banks_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "banks"
    directory.mkdir()
    for name in ("hdfc.v1.yaml", "icici.v1.yaml"):
        shutil.copy(config_dir() / "banks" / name, directory / name)
    return directory


@pytest.fixture()
def registry(banks_dir: Path) -> BankConfigRegistry:
    return BankConfigRegistry(banks_dir, check_interval_seconds=3600)


@pytest.fixture()
def client(
    db_session: Session, registry: BankConfigRegistry, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setattr("recon.api.v1.admin.get_registry", lambda: registry)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_now] = lambda: _NOW
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _key(session: Session, role: str) -> dict[str, str]:
    raw = generate_api_key()
    session.add(ApiKey(key_hash=hash_api_key(raw), name=f"k-{uuid.uuid4().hex[:8]}", role=role))
    session.flush()
    return {"X-API-Key": raw}


def _edit(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text().replace(old, new))
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


# -- the config store ------------------------------------------------------------------


def test_the_first_sync_records_every_bank_as_active(
    db_session: Session, registry: BankConfigRegistry
) -> None:
    changes = sync_bank_configurations(db_session, registry, loaded_by="system:test", now=_NOW)
    assert sorted((c.bank_code, c.kind) for c in changes) == [("HDFC", "ADDED"), ("ICICI", "ADDED")]
    rows = db_session.scalars(select(BankConfiguration)).all()
    assert {r.is_active for r in rows} == {True}
    hdfc = next(r for r in rows if r.bank_code == "HDFC")
    assert hdfc.config_json["bank_code"] == "HDFC" and len(hdfc.content_sha256) == 64


def test_syncing_an_unchanged_registry_does_nothing(
    db_session: Session, registry: BankConfigRegistry
) -> None:
    sync_bank_configurations(db_session, registry, loaded_by="system:test", now=_NOW)
    assert sync_bank_configurations(db_session, registry, loaded_by="system:test", now=_NOW) == []
    assert db_session.query(BankConfiguration).count() == 2


def test_an_edit_creates_a_new_active_version_and_keeps_the_old_one_for_history(
    db_session: Session, registry: BankConfigRegistry, banks_dir: Path
) -> None:
    sync_bank_configurations(db_session, registry, loaded_by="system:test", now=_NOW)
    _edit(
        banks_dir / "hdfc.v1.yaml", "reconciliation_window_days: 1", "reconciliation_window_days: 3"
    )
    registry.reload()

    changes = sync_bank_configurations(db_session, registry, loaded_by="api-key:admin", now=_NOW)

    assert [(c.bank_code, c.kind) for c in changes] == [("HDFC", "UPDATED")]
    versions = db_session.scalars(
        select(BankConfiguration).where(BankConfiguration.bank_code == "HDFC")
    ).all()
    assert sorted(v.is_active for v in versions) == [False, True]
    active = next(v for v in versions if v.is_active)
    assert active.config_json["reconciliation_window_days"] == 3


def test_reverting_an_edit_reactivates_the_earlier_version(
    db_session: Session, registry: BankConfigRegistry, banks_dir: Path
) -> None:
    sync_bank_configurations(db_session, registry, loaded_by="system:test", now=_NOW)
    _edit(
        banks_dir / "hdfc.v1.yaml", "reconciliation_window_days: 1", "reconciliation_window_days: 3"
    )
    registry.reload()
    sync_bank_configurations(db_session, registry, loaded_by="system:test", now=_NOW)
    _edit(
        banks_dir / "hdfc.v1.yaml", "reconciliation_window_days: 3", "reconciliation_window_days: 1"
    )
    registry.reload()

    changes = sync_bank_configurations(db_session, registry, loaded_by="system:test", now=_NOW)

    assert [(c.bank_code, c.kind) for c in changes] == [("HDFC", "REACTIVATED")]
    assert (
        db_session.query(BankConfiguration).filter_by(bank_code="HDFC").count() == 2
    )  # no duplicate row


def test_every_config_change_is_on_the_audit_chain(
    db_session: Session, registry: BankConfigRegistry
) -> None:
    sync_bank_configurations(db_session, registry, loaded_by="system:test", now=_NOW)
    entries = db_session.scalars(select(AuditLog).where(AuditLog.chain_id == CONFIG_CHAIN)).all()
    assert len(entries) == 2 and {e.action_type for e in entries} == {"CONFIG_CHANGE"}
    assert {e.affected_records["bank_code"] for e in entries} == {"HDFC", "ICICI"}
    assert verify_chain(db_session, CONFIG_CHAIN).ok is True


# -- the admin endpoints -------------------------------------------------------------------


def test_any_viewer_can_read_the_live_configuration(
    client: TestClient, db_session: Session
) -> None:
    body = client.get("/api/v1/admin/banks", headers=_key(db_session, "VIEWER")).json()
    assert [b["bank_code"] for b in body["banks"]] == ["HDFC", "ICICI"]
    assert body["last_error"] is None and body["banks"][0]["recorded_in_database"] is False


def test_an_admin_reload_applies_a_changed_file_and_records_the_version(
    client: TestClient, db_session: Session, banks_dir: Path
) -> None:
    admin = _key(db_session, "ADMIN")
    client.post("/api/v1/admin/banks/reload", headers=admin)  # baseline: records the first versions
    _edit(
        banks_dir / "icici.v1.yaml",
        "reconciliation_window_days: 1",
        "reconciliation_window_days: 2",
    )

    body = client.post("/api/v1/admin/banks/reload", headers=admin).json()

    assert (body["changed"], body["updated"], body["error"]) == (True, ["ICICI"], None)
    assert body["recorded"] == ["ICICI:icici.v1:UPDATED"]
    listing = client.get("/api/v1/admin/banks", headers=admin).json()
    icici = next(b for b in listing["banks"] if b["bank_code"] == "ICICI")
    assert (icici["reconciliation_window_days"], icici["recorded_in_database"]) == (2, True)
    versions = client.get("/api/v1/admin/banks/ICICI/versions", headers=admin).json()
    assert [v["is_active"] for v in versions] == [True, False]


def test_a_broken_edit_is_reported_and_the_old_configuration_stays_live(
    client: TestClient, db_session: Session, banks_dir: Path
) -> None:
    admin = _key(db_session, "ADMIN")
    _edit(banks_dir / "hdfc.v1.yaml", "timezone: Asia/Kolkata", "timezone: Not/AZone")
    body = client.post("/api/v1/admin/banks/reload", headers=admin).json()
    assert body["changed"] is False and "Not/AZone" in body["error"]
    listing = client.get("/api/v1/admin/banks", headers=admin).json()
    assert listing["last_error"] is not None
    assert (
        next(b for b in listing["banks"] if b["bank_code"] == "HDFC")["timezone"] == "Asia/Kolkata"
    )


def test_reloading_needs_an_admin(client: TestClient, db_session: Session) -> None:
    assert (
        client.post("/api/v1/admin/banks/reload", headers=_key(db_session, "ANALYST")).status_code
        == 403
    )


def test_an_analyst_can_validate_a_candidate_configuration_against_a_sample(
    client: TestClient, db_session: Session
) -> None:
    sample = (
        "Txn Ref No,Txn Date,Txn Amount,Type,Party Name,Remarks\nUTR1,2026-03-15,100.00,CR,Acme,x\n"
    )
    response = client.post(
        "/api/v1/admin/banks/validate",
        headers=_key(db_session, "ANALYST"),
        files={"config": ("icici.yaml", _ICICI_YAML), "sample": ("sample.csv", sample)},
    )
    body = response.json()
    assert response.status_code == 200 and body["ok"] is True
    assert (
        body["sample"]["rows_checked"] == 1 and body["sample"]["preview"][0]["reference"] == "UTR1"
    )
    assert db_session.query(BankConfiguration).count() == 0  # nothing was stored


def test_validation_flags_a_sample_that_no_longer_fits(
    client: TestClient, db_session: Session
) -> None:
    sample = "Txn Ref No,Tax Deducted,Net Amount,Txn Amount,Type\nUTR1,5,95,100.00,CR\n"
    body = client.post(
        "/api/v1/admin/banks/validate",
        headers=_key(db_session, "ANALYST"),
        files={"config": ("icici.yaml", _ICICI_YAML), "sample": ("sample.csv", sample)},
    ).json()
    assert body["ok"] is False
    assert body["sample"]["missing_columns"] == ["Txn Date", "Party Name", "Remarks"]
    assert body["sample"]["new_columns"] == ["Tax Deducted", "Net Amount"]


def test_validation_rejects_an_invalid_configuration_without_raising(
    client: TestClient, db_session: Session
) -> None:
    body = client.post(
        "/api/v1/admin/banks/validate",
        headers=_key(db_session, "ANALYST"),
        files={"config": ("bad.yaml", "bank_code: X\n")},
    ).json()
    assert body["ok"] is False and "schema validation failed" in body["config_errors"][0]


def test_validation_is_not_open_to_a_viewer(client: TestClient, db_session: Session) -> None:
    response = client.post(
        "/api/v1/admin/banks/validate",
        headers=_key(db_session, "VIEWER"),
        files={"config": ("c.yaml", _ICICI_YAML)},
    )
    assert response.status_code == 403


# -- users -----------------------------------------------------------------------------------


def test_an_admin_creates_a_user_issues_a_key_and_the_key_works(
    client: TestClient, db_session: Session
) -> None:
    admin = _key(db_session, "ADMIN")
    created = client.post(
        "/api/v1/admin/users",
        headers=admin,
        json={"username": "priya", "role": "ANALYST", "email": "p@example.com"},
    )
    assert created.status_code == 201 and created.json()["is_active"] is True
    user_id = created.json()["id"]

    issued = client.post(
        f"/api/v1/admin/users/{user_id}/api-keys", headers=admin, json={"label": "laptop"}
    )

    assert issued.status_code == 201
    body = issued.json()
    assert (body["name"], body["role"]) == ("priya:laptop", "ANALYST") and body[
        "api_key"
    ].startswith("rk_")
    stored = db_session.scalars(select(ApiKey).where(ApiKey.name == "priya:laptop")).one()
    assert stored.key_hash == hash_api_key(body["api_key"]) and str(stored.user_id) == user_id
    assert (
        client.get("/api/v1/exceptions", headers={"X-API-Key": body["api_key"]}).status_code == 200
    )


def test_deactivating_a_user_revokes_their_keys_at_once(
    client: TestClient, db_session: Session
) -> None:
    admin = _key(db_session, "ADMIN")
    user_id = client.post(
        "/api/v1/admin/users", headers=admin, json={"username": "priya", "role": "ANALYST"}
    ).json()["id"]
    raw = client.post(f"/api/v1/admin/users/{user_id}/api-keys", headers=admin, json={}).json()[
        "api_key"
    ]
    assert client.get("/api/v1/exceptions", headers={"X-API-Key": raw}).status_code == 200

    done = client.post(f"/api/v1/admin/users/{user_id}/deactivate", headers=admin)

    assert done.status_code == 200 and done.json()["is_active"] is False
    assert client.get("/api/v1/exceptions", headers={"X-API-Key": raw}).status_code == 401
    assert (
        client.post(f"/api/v1/admin/users/{user_id}/api-keys", headers=admin, json={}).status_code
        == 409
    )
    key = db_session.scalars(select(ApiKey).where(ApiKey.user_id == uuid.UUID(user_id))).one()
    assert key.revoked_at == _NOW


def test_user_changes_are_audited_and_the_chain_verifies(
    client: TestClient, db_session: Session
) -> None:
    admin = _key(db_session, "ADMIN")
    user_id = client.post(
        "/api/v1/admin/users", headers=admin, json={"username": "priya", "role": "VIEWER"}
    ).json()["id"]
    client.post(f"/api/v1/admin/users/{user_id}/api-keys", headers=admin, json={})
    client.post(f"/api/v1/admin/users/{user_id}/deactivate", headers=admin)
    entries = db_session.scalars(
        select(AuditLog).where(AuditLog.chain_id == USERS_CHAIN).order_by(AuditLog.sequence_no)
    ).all()
    assert [e.rationale.split()[0] for e in entries] == ["user", "API", "user"]
    assert all(
        "rk_" not in str(e.after_state) for e in entries
    )  # a key is never written to the trail
    assert verify_chain(db_session, USERS_CHAIN).ok is True


def test_user_administration_is_admin_only_and_validated(
    client: TestClient, db_session: Session
) -> None:
    analyst = _key(db_session, "ANALYST")
    assert (
        client.post(
            "/api/v1/admin/users", headers=analyst, json={"username": "x1", "role": "VIEWER"}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/admin/users", headers=analyst).status_code == 403
    admin = _key(db_session, "ADMIN")
    assert (
        client.post(
            "/api/v1/admin/users", headers=admin, json={"username": "bad name!", "role": "VIEWER"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/admin/users", headers=admin, json={"username": "ok.user", "role": "GOD"}
        ).status_code
        == 422
    )
    first = client.post(
        "/api/v1/admin/users", headers=admin, json={"username": "dup", "role": "VIEWER"}
    )
    again = client.post(
        "/api/v1/admin/users", headers=admin, json={"username": "dup", "role": "VIEWER"}
    )
    assert (first.status_code, again.status_code) == (201, 409)
    assert client.get("/api/v1/admin/users", headers=admin).json()["total"] >= 1


def test_unknown_users_are_404_and_duplicate_key_labels_409(
    client: TestClient, db_session: Session
) -> None:
    admin = _key(db_session, "ADMIN")
    assert (
        client.post(
            f"/api/v1/admin/users/{uuid.uuid4()}/api-keys", headers=admin, json={}
        ).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/admin/users/{uuid.uuid4()}/deactivate", headers=admin).status_code
        == 404
    )
    user_id = client.post(
        "/api/v1/admin/users", headers=admin, json={"username": "priya", "role": "VIEWER"}
    ).json()["id"]
    assert (
        client.post(
            f"/api/v1/admin/users/{user_id}/api-keys", headers=admin, json={"label": "a"}
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"/api/v1/admin/users/{user_id}/api-keys", headers=admin, json={"label": "a"}
        ).status_code
        == 409
    )
    assert db_session.query(User).filter_by(username="priya").count() == 1
