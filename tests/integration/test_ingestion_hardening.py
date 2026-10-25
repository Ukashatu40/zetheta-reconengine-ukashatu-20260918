"""Ingestion hardening through the real upload pipeline: card-number masking (C6),
the B4.1 format change, balance-level reconciliation (C5), file re-verification (C4)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.anomaly.config import default_anomaly_config
from recon.anomaly.service import AnomalyService
from recon.api.deps import get_clock, get_db, get_now
from recon.api.main import app
from recon.api.v1.upload import get_upload_dir
from recon.audit.chains import INGESTION_CHAIN
from recon.audit.verifier import verify_chain
from recon.config.registry import default_bank_configs
from recon.persistence.models import (
    Alert,
    ApiKey,
    AuditLog,
    IngestionFile,
    NormalisedTransaction,
    RawTransaction,
    StatementBalance,
)
from recon.security.api_keys import generate_api_key, hash_api_key
from tests.integration.factories import make_ingestion_file

_NOW = datetime(2026, 3, 16, 12, 0, tzinfo=UTC)
_PAN = "4111111111111111"

_ICICI_HEADER = "Txn Ref No,Txn Date,Txn Amount,Type,Party Name,Remarks"


@pytest.fixture()
def upload_dir(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


@pytest.fixture()
def client(db_session: Session, upload_dir: Path) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_now] = lambda: _NOW
    app.dependency_overrides[get_clock] = lambda: (lambda: _NOW)
    app.dependency_overrides[get_upload_dir] = lambda: upload_dir
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _key(session: Session, role: str = "ANALYST") -> dict[str, str]:
    raw = generate_api_key()
    session.add(ApiKey(key_hash=hash_api_key(raw), name=f"k-{uuid.uuid4().hex[:8]}", role=role))
    session.flush()
    return {"X-API-Key": raw}


def _upload(
    client: TestClient,
    headers: dict[str, str],
    content: str,
    *,
    bank: str,
    fmt: str,
    name: str,
    source: str = "EXTERNAL",
) -> Any:
    return client.post(
        "/api/v1/files/upload",
        headers=headers,
        data={"bank_code": bank, "source": source, "format_type": fmt},
        files={"file": (name, content, "text/plain")},
    )


def _mt940(closing: str = "1250,00", narration: str = "Payment from Acme") -> str:
    return (
        ":20:STMT001\n:25:12345678\n:28C:00001/001\n:60F:C260315INR1000,00\n"
        f":61:2603160316C300,00NTRFREF0001//BANKREF1\n:86:{narration}\n"
        ":61:2603160316D50,00NTRFREF0002//BANKREF2\n:86:Bank charges\n"
        f":62F:C260316INR{closing}\n"
    )


# -- card numbers (C6, AE-26) ----------------------------------------------------------------


def test_a_card_number_in_a_narration_is_masked_before_anything_is_stored(
    client: TestClient, db_session: Session
) -> None:
    response = _upload(
        client,
        _key(db_session),
        _mt940(narration=f"Card {_PAN} used"),
        bank="SBI",
        fmt="MT940",
        name="sbi.sta",
    )

    body = response.json()
    assert response.status_code == 201 and body["pan_masked_count"] == 1
    stored = " ".join(
        str(r.raw_payload) for r in db_session.scalars(select(RawTransaction))
    ) + " ".join(str(n.narration) for n in db_session.scalars(select(NormalisedTransaction)))
    assert _PAN not in stored
    assert "************1111" in stored


def test_the_masked_upload_raises_a_compliance_alert_that_never_contains_the_number(
    client: TestClient, db_session: Session
) -> None:
    _upload(
        client,
        _key(db_session),
        _mt940(narration=f"Card {_PAN} used"),
        bank="SBI",
        fmt="MT940",
        name="sbi.sta",
    )
    alert = db_session.scalars(select(Alert).where(Alert.alert_type == "PAN_EXPOSURE")).one()
    assert (alert.severity, alert.route_to) == ("HIGH", "compliance")
    assert _PAN not in alert.message and _PAN not in str(alert.details)
    assert alert.details["masked_count"] == 1


def test_a_file_without_card_numbers_raises_no_pan_alert(
    client: TestClient, db_session: Session
) -> None:
    body = _upload(
        client, _key(db_session), _mt940(), bank="SBI", fmt="MT940", name="sbi.sta"
    ).json()
    assert body["pan_masked_count"] == 0
    assert db_session.query(Alert).filter_by(alert_type="PAN_EXPOSURE").count() == 0


# -- balance-level reconciliation (C5) ------------------------------------------------------------


def test_a_statement_that_balances_is_recorded_as_balanced(
    client: TestClient, db_session: Session
) -> None:
    body = _upload(
        client, _key(db_session), _mt940(), bank="SBI", fmt="MT940", name="sbi.sta"
    ).json()
    assert body["statement_balanced"] is True
    row = db_session.scalars(select(StatementBalance)).one()
    assert (row.opening_minor, row.closing_minor, row.computed_closing_minor) == (
        100_000,
        125_000,
        125_000,
    )
    assert (row.balanced, row.difference_minor, row.entry_count) == (True, 0, 2)
    assert db_session.query(Alert).filter_by(alert_type="BALANCE_MISMATCH").count() == 0


def test_a_statement_that_does_not_balance_is_a_critical_alert(
    client: TestClient, db_session: Session
) -> None:
    body = _upload(
        client, _key(db_session), _mt940(closing="1300,00"), bank="SBI", fmt="MT940", name="sbi.sta"
    ).json()
    assert body["statement_balanced"] is False and body["alerts_raised"] >= 1
    row = db_session.scalars(select(StatementBalance)).one()
    assert (row.balanced, row.difference_minor) == (False, 5_000)
    alert = db_session.scalars(select(Alert).where(Alert.alert_type == "BALANCE_MISMATCH")).one()
    assert (alert.severity, alert.route_to) == ("CRITICAL", "compliance")
    assert alert.details["difference_minor"] == 5_000 and alert.details["entry_count"] == 2


def test_balance_failing_while_transactions_pass_is_the_c5_critical_signal(
    client: TestClient, db_session: Session
) -> None:
    _upload(
        client, _key(db_session), _mt940(closing="1300,00"), bank="SBI", fmt="MT940", name="sbi.sta"
    )
    service = AnomalyService(db_session, default_anomaly_config(), default_bank_configs())
    passing = {"external_pool": 10, "internal_pool": 10, "exceptions_created": 0}
    raised = service.evaluate_run(
        run_id="run-c5",
        bank_code="SBI",
        metrics=passing | {"overall_match_rate_of_total": 0.99},
        now=_NOW,
    )
    assert raised == {"BALANCE_TXN_DIVERGENCE": 1}
    # a poorly matched run is a different problem: no C5 alert
    poor = service.evaluate_run(
        run_id="run-low",
        bank_code="SBI",
        metrics=passing | {"overall_match_rate_of_total": 0.5},
        now=_NOW,
    )
    assert poor == {}


def test_a_camt_statement_is_balance_checked_too(client: TestClient, db_session: Session) -> None:
    camt = """<?xml version="1.0" encoding="UTF-8"?>
<BkToCstmrStmt xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
  <GrpHdr><MsgId>MSG1</MsgId></GrpHdr>
  <Stmt>
    <Id>STMT1</Id>
    <Acct><Id><IBAN>IN00KOTAK1234567890</IBAN></Id></Acct>
    <Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><CdtDbtInd>CRDT</CdtDbtInd>
      <Amt Ccy="INR">1000.00</Amt><Dt><Dt>2026-03-15</Dt></Dt></Bal>
    <Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><CdtDbtInd>CRDT</CdtDbtInd>
      <Amt Ccy="INR">1300.00</Amt><Dt><Dt>2026-03-16</Dt></Dt></Bal>
    <Ntry><Amt Ccy="INR">300.00</Amt><CdtDbtInd>CRDT</CdtDbtInd>
      <BookgDt><Dt>2026-03-16</Dt></BookgDt><ValDt><Dt>2026-03-16</Dt></ValDt>
      <NtryDtls><TxDtls><Refs><EndToEndId>E2E0001</EndToEndId></Refs></TxDtls></NtryDtls></Ntry>
  </Stmt>
</BkToCstmrStmt>"""
    body = _upload(
        client, _key(db_session), camt, bank="KOTAK", fmt="CAMT053", name="kotak.xml"
    ).json()
    assert body["statement_balanced"] is True
    assert db_session.scalars(select(StatementBalance)).one().account == "IN00KOTAK1234567890"


# -- B4.1: the midnight format change ------------------------------------------------


def test_b41_a_file_missing_a_mapped_column_is_rejected_and_alerts_before_any_record_is_written(
    client: TestClient, db_session: Session, upload_dir: Path
) -> None:
    # ICICI moved the date column and inserted two new columns; here the date column is gone
    csv = (
        "Txn Ref No,Tax Deducted,Net Amount,Txn Amount,Type,Party Name,Remarks\n"
        "UTR1,5,95,100.00,CR,Acme,x\n"
    )
    response = _upload(client, _key(db_session), csv, bank="ICICI", fmt="CSV", name="icici.csv")

    assert response.status_code == 422
    assert "missing columns: Txn Date" in response.json()["detail"]
    assert (
        db_session.query(IngestionFile).count() == 0
        and db_session.query(RawTransaction).count() == 0
    )
    assert not list(upload_dir.glob("*"))  # the rejected file is not kept
    alert = db_session.scalars(
        select(Alert).where(Alert.alert_type == "FORMAT_CHANGE_DETECTED")
    ).one()
    assert (alert.severity, alert.details["blocked"]) == ("CRITICAL", True)
    assert alert.details["missing_columns"] == ["Txn Date"]
    assert alert.details["new_columns"] == ["Tax Deducted", "Net Amount"]


def test_a_repeat_of_the_same_bad_layout_does_not_raise_a_second_alert(
    client: TestClient, db_session: Session
) -> None:
    csv = "Txn Ref No,Tax Deducted\nUTR1,5\n"
    headers = _key(db_session)
    _upload(client, headers, csv, bank="ICICI", fmt="CSV", name="a.csv")
    _upload(client, headers, csv + "UTR2,6\n", bank="ICICI", fmt="CSV", name="b.csv")
    assert db_session.query(Alert).filter_by(alert_type="FORMAT_CHANGE_DETECTED").count() == 1


def test_new_columns_alone_ingest_the_file_and_raise_a_medium_alert(
    client: TestClient, db_session: Session
) -> None:
    csv = f"{_ICICI_HEADER},Tax Deducted\nUTR1,2026-03-15,100.00,CR,Acme,x,5\n"
    response = _upload(client, _key(db_session), csv, bank="ICICI", fmt="CSV", name="icici.csv")
    assert response.status_code == 201 and response.json()["record_count"] == 1
    alert = db_session.scalars(
        select(Alert).where(Alert.alert_type == "FORMAT_CHANGE_DETECTED")
    ).one()
    assert (alert.severity, alert.details["blocked"]) == ("MEDIUM", False)


def test_a_reordered_but_complete_file_ingests_with_a_medium_alert(
    client: TestClient, db_session: Session
) -> None:
    csv = (
        "Txn Date,Txn Ref No,Txn Amount,Type,Party Name,Remarks\n2026-03-15,UTR1,100.00,CR,Acme,x\n"
    )
    response = _upload(client, _key(db_session), csv, bank="ICICI", fmt="CSV", name="icici.csv")
    assert response.status_code == 201
    assert response.json()["normalised_count"] == 1  # mapping is by name, so the data is right
    assert db_session.query(Alert).filter_by(alert_type="FORMAT_CHANGE_DETECTED").count() == 1


def test_an_unchanged_file_raises_no_format_alert(client: TestClient, db_session: Session) -> None:
    csv = f"{_ICICI_HEADER}\nUTR1,2026-03-15,100.00,CR,Acme,x\n"
    _upload(client, _key(db_session), csv, bank="ICICI", fmt="CSV", name="icici.csv")
    assert db_session.query(Alert).filter_by(alert_type="FORMAT_CHANGE_DETECTED").count() == 0


# -- file re-verification (C4) -----------------------------------------------------


def _ingested(
    client: TestClient, db_session: Session, headers: dict[str, str]
) -> tuple[uuid.UUID, Path]:
    body = _upload(client, headers, _mt940(), bank="SBI", fmt="MT940", name="sbi.sta").json()
    row = db_session.get(IngestionFile, uuid.UUID(body["ingestion_file_id"]))
    assert row is not None and row.stored_path is not None
    return row.id, Path(row.stored_path)


def test_an_untouched_file_verifies_ok_and_is_audited(
    client: TestClient, db_session: Session
) -> None:
    headers = _key(db_session)
    file_id, _ = _ingested(client, db_session, headers)
    body = client.post(f"/api/v1/files/{file_id}/verify", headers=headers).json()
    assert body["status"] == "OK" and body["actual_sha256"] == body["expected_sha256"]
    row = db_session.get(IngestionFile, file_id)
    assert row is not None and (row.integrity_status, row.integrity_checked_at) == ("OK", _NOW)
    entry = db_session.scalars(
        select(AuditLog).where(
            AuditLog.chain_id == INGESTION_CHAIN, AuditLog.action_type == "FILE_VERIFY"
        )
    ).one()
    assert entry.after_state is not None and entry.after_state["integrity_status"] == "OK"
    assert verify_chain(db_session, INGESTION_CHAIN).ok is True


def test_a_file_edited_after_ingestion_is_tampered_and_critical(
    client: TestClient, db_session: Session
) -> None:
    headers = _key(db_session)
    file_id, path = _ingested(client, db_session, headers)
    path.write_text(path.read_text().replace("300,00", "900,00"))

    body = client.post(f"/api/v1/files/{file_id}/verify", headers=headers).json()

    assert body["status"] == "TAMPERED" and body["actual_sha256"] != body["expected_sha256"]
    alert = db_session.scalars(select(Alert).where(Alert.alert_type == "FILE_TAMPERED")).one()
    assert (alert.severity, alert.route_to) == ("CRITICAL", "compliance")
    assert alert.details["expected_sha256"] == body["expected_sha256"]


def test_a_missing_file_is_reported_as_missing(client: TestClient, db_session: Session) -> None:
    headers = _key(db_session)
    file_id, path = _ingested(client, db_session, headers)
    path.unlink()
    assert (
        client.post(f"/api/v1/files/{file_id}/verify", headers=headers).json()["status"]
        == "MISSING"
    )
    assert db_session.query(Alert).filter_by(alert_type="FILE_TAMPERED").count() == 1


def test_a_file_whose_bytes_were_not_kept_cannot_be_verified(
    client: TestClient, db_session: Session
) -> None:
    f = make_ingestion_file(db_session)
    response = client.post(f"/api/v1/files/{f.id}/verify", headers=_key(db_session))
    assert response.status_code == 409 and "not kept" in response.json()["detail"]
    assert (
        client.post(f"/api/v1/files/{uuid.uuid4()}/verify", headers=_key(db_session)).status_code
        == 404
    )


def test_verify_all_checks_every_stored_file_and_needs_an_admin(
    client: TestClient, db_session: Session
) -> None:
    analyst = _key(db_session)
    _, first = _ingested(client, db_session, analyst)
    other = _upload(
        client, analyst, _mt940(closing="1300,00"), bank="SBI", fmt="MT940", name="two.sta"
    ).json()
    second = db_session.get(IngestionFile, uuid.UUID(other["ingestion_file_id"]))
    assert second is not None and second.stored_path is not None
    first.write_text("changed")

    assert client.post("/api/v1/files/verify", headers=analyst).status_code == 403
    body = client.post("/api/v1/files/verify", headers=_key(db_session, "ADMIN")).json()
    assert body == {"checked": 2, "ok": 1, "tampered": 1, "missing": 0}


def test_the_file_monitor_lists_hash_counts_and_integrity(
    client: TestClient, db_session: Session
) -> None:
    headers = _key(db_session, "VIEWER")
    _ingested(client, db_session, _key(db_session))
    listed = client.get(
        "/api/v1/files?bank_code=SBI&source=EXTERNAL&format_type=MT940", headers=headers
    ).json()
    item = listed["items"][0]
    assert listed["total"] == 1
    assert (item["original_filename"], item["record_count"], item["parse_error_count"]) == (
        "sbi.sta",
        2,
        0,
    )
    assert len(item["content_sha256"]) == 64 and item["source"] == "EXTERNAL"
    assert client.get("/api/v1/files?bank_code=ICICI", headers=headers).json()["total"] == 0
    one = client.get(f"/api/v1/files/{item['id']}", headers=headers)
    assert (
        one.status_code == 200
        and client.get(f"/api/v1/files/{uuid.uuid4()}", headers=headers).status_code == 404
    )


def test_every_upload_audits_both_ingest_and_normalise(
    client: TestClient, db_session: Session
) -> None:
    _ingested(client, db_session, _key(db_session))
    actions = [
        e.action_type
        for e in db_session.scalars(
            select(AuditLog)
            .where(AuditLog.chain_id == INGESTION_CHAIN)
            .order_by(AuditLog.sequence_no)
        )
    ]
    assert actions == ["INGEST", "NORMALISE"]
