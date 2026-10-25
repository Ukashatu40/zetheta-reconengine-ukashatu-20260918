"""One-to-one cross-currency matching, the FX service, and the FX endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.api.deps import get_clock, get_db
from recon.api.main import app
from recon.audit.chains import INGESTION_CHAIN, MATCHING_CHAIN
from recon.audit.logger import AuditLogger
from recon.fx.service import FxService
from recon.matching.claims import ClaimsService
from recon.matching.strategies.cross_currency import CrossCurrencyStrategy
from recon.persistence.models import ApiKey, AuditLog, FxRate, MatchResult, NormalisedTransaction
from recon.security.api_keys import generate_api_key, hash_api_key
from tests.integration.factories import make_ingestion_file, make_txn

_DAY = date(2026, 3, 15)
_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _rate(
    session: Session, currency: str, rate: str, day: date = _DAY, source: str = "RBI"
) -> None:
    session.add(FxRate(rate_date=day, currency=currency, rate_inr=Decimal(rate), source=source))
    session.flush()


def _txn(session: Session, source: str, ref: str, minor: int, currency: str, **extra: Any) -> Any:
    f = make_ingestion_file(session)
    values: dict[str, Any] = {
        "txn_date": _DAY,
        "txn_id": ref,
        "normalised_reference": ref,
        "amount": Decimal(minor) / 100,
        "amount_minor": minor,
        "currency": currency,
        **extra,
    }
    txn = make_txn(f.id, source=source, **values)
    session.add(txn)
    session.flush()
    return txn


def _strategy(
    session: Session, tolerance: str = "1.5", *, audit: bool = False
) -> CrossCurrencyStrategy:
    return CrossCurrencyStrategy(
        session,
        "run-cc",
        FxService(session),
        tolerance_percent=Decimal(tolerance),
        audit=AuditLogger(session) if audit else None,
        now=_NOW if audit else None,
    )


# -- FxService -------------------------------------------------------------------


def test_inr_needs_no_rate_and_a_stored_rate_is_returned(db_session: Session) -> None:
    _rate(db_session, "USD", "83.10")
    service = FxService(db_session)
    inr = service.quote("INR", _DAY)
    usd = service.quote("USD", _DAY)
    assert inr is not None and usd is not None
    assert (inr.rate_inr, usd.rate_inr, usd.source) == (Decimal(1), Decimal("83.10"), "RBI")


def test_the_service_uses_a_recent_rate_within_the_window_and_reports_its_age(
    db_session: Session,
) -> None:
    _rate(db_session, "USD", "83.10", day=_DAY - timedelta(days=2))
    quote = FxService(db_session).quote("USD", _DAY)
    assert quote is not None and quote.days_stale == 2
    assert FxService(db_session, max_stale_days=1).quote("USD", _DAY) is None


def test_the_service_prefers_rbi_over_fbil_over_card_rates(db_session: Session) -> None:
    _rate(db_session, "EUR", "90.0", source="BANK_CARD")
    _rate(db_session, "EUR", "90.5", source="FBIL")
    _rate(db_session, "EUR", "91.0", source="RBI")
    quote = FxService(db_session).quote("EUR", _DAY)
    assert quote is not None and (quote.source, quote.rate_inr) == ("RBI", Decimal("91.00000000"))


# -- one-to-one cross-currency ---------------------------------------------------


def test_a_usd_external_matches_an_inr_internal_within_the_fx_tolerance(
    db_session: Session,
) -> None:
    _rate(db_session, "USD", "83.40")
    internal = _txn(db_session, "INTERNAL", "INV900", 10_000_000, "INR")  # INR 1,00,000
    external = _txn(db_session, "EXTERNAL", "INV900", 120_000, "USD")  # USD 1,200 = INR 100,080

    outcome = _strategy(db_session).run([internal], [external])

    assert outcome.matched_count == 1
    row = db_session.scalars(select(MatchResult)).one()
    assert (row.match_type, row.rule_id, row.status) == (
        "CROSS_CURRENCY",
        "CROSS_CURRENCY_TOLERANCE",
        "AUTO_MATCHED",
    )
    assert row.matched_fields["fx"]["variance_percent"] == "0.0800"
    assert {internal.match_status, external.match_status} == {"MATCHED"}


def test_a_pair_beyond_the_tolerance_is_left_for_the_classifier(db_session: Session) -> None:
    _rate(db_session, "USD", "83.40")
    internal = _txn(db_session, "INTERNAL", "INV900", 10_000_000, "INR")
    external = _txn(db_session, "EXTERNAL", "INV900", 130_000, "USD")  # 8% out
    outcome = _strategy(db_session).run([internal], [external])
    assert (outcome.matched_count, outcome.out_of_tolerance_count) == (0, 1)
    assert external.match_status == "UNMATCHED"


def test_the_interbank_tolerance_is_tighter(db_session: Session) -> None:
    _rate(db_session, "USD", "83.40")
    internal = _txn(db_session, "INTERNAL", "INV900", 10_000_000, "INR")
    external = _txn(db_session, "EXTERNAL", "INV900", 120_800, "USD")  # 0.72% off
    assert _strategy(db_session, "1.5").run([internal], [external]).matched_count == 1
    other_int = _txn(db_session, "INTERNAL", "INV901", 10_000_000, "INR")
    other_ext = _txn(db_session, "EXTERNAL", "INV901", 120_800, "USD")
    assert _strategy(db_session, "0.5").run([other_int], [other_ext]).out_of_tolerance_count == 1


def test_two_internals_that_fit_are_ambiguous(db_session: Session) -> None:
    _rate(db_session, "USD", "83.40")
    a = _txn(db_session, "INTERNAL", "INV900", 10_000_000, "INR")
    b = _txn(db_session, "INTERNAL", "INV900", 10_001_000, "INR")
    external = _txn(db_session, "EXTERNAL", "INV900", 120_000, "USD")
    outcome = _strategy(db_session).run([a, b], [external])
    assert (outcome.matched_count, outcome.ambiguous_count) == (0, 1)


def test_a_missing_rate_is_counted(db_session: Session) -> None:
    internal = _txn(db_session, "INTERNAL", "INV900", 10_000_000, "INR")
    external = _txn(db_session, "EXTERNAL", "INV900", 120_000, "USD")
    assert _strategy(db_session).run([internal], [external]).missing_fx_count == 1


@pytest.mark.parametrize(
    "override",
    [{"direction": "DR"}, {"txn_date": date(2026, 3, 25)}, {"normalised_reference": "OTHER"}],
)
def test_a_pair_that_is_not_the_same_payment_is_never_compared(
    db_session: Session, override: dict[str, Any]
) -> None:
    _rate(db_session, "USD", "83.40")
    internal = _txn(db_session, "INTERNAL", "INV900", 10_000_000, "INR")
    external = _txn(db_session, "EXTERNAL", "INV900", 120_000, "USD", **override)
    outcome = _strategy(db_session).run([internal], [external])
    assert (outcome.matched_count, outcome.out_of_tolerance_count, outcome.ambiguous_count) == (
        0,
        0,
        0,
    )


def test_the_match_is_audited_with_both_rates(db_session: Session) -> None:
    _rate(db_session, "USD", "83.40")
    internal = _txn(db_session, "INTERNAL", "INV900", 10_000_000, "INR")
    external = _txn(db_session, "EXTERNAL", "INV900", 120_000, "USD")
    _strategy(db_session, audit=True).run([internal], [external])
    entry = db_session.scalars(select(AuditLog).where(AuditLog.chain_id == MATCHING_CHAIN)).one()
    assert entry.after_state is not None
    assert entry.after_state["fx"]["rates"]["USD@2026-03-15"]["rate_inr"] == "83.40000000"
    assert entry.actor_id == "cross-currency-matcher"


def test_a_claimed_internal_is_skipped_without_error(db_session: Session) -> None:
    _rate(db_session, "USD", "83.40")
    internal = _txn(db_session, "INTERNAL", "INV900", 10_000_000, "INR")
    external = _txn(db_session, "EXTERNAL", "INV900", 120_000, "USD")
    ClaimsService(db_session).claim(internal.id, "INTERNAL")
    outcome = _strategy(db_session).run([internal], [external])
    assert (outcome.matched_count, outcome.skipped_claim_conflict_count) == (0, 1)


def test_an_audit_logger_requires_a_clock(db_session: Session) -> None:
    with pytest.raises(ValueError, match="now is required"):
        CrossCurrencyStrategy(
            db_session,
            "r",
            FxService(db_session),
            tolerance_percent=Decimal("1.5"),
            audit=AuditLogger(db_session),
        )


# -- FX endpoints ----------------------------------------------------------------


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_clock] = lambda: (lambda: _NOW)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _headers(session: Session, role: str) -> dict[str, str]:
    raw = generate_api_key()
    session.add(ApiKey(key_hash=hash_api_key(raw), name=f"k-{uuid.uuid4().hex[:8]}", role=role))
    session.flush()
    return {"X-API-Key": raw}


def _sample_fx_file() -> bytes:
    """D10 shape: 15 days x 30 currencies = 450 rates, three of them wrong."""
    codes = [f"{chr(65 + i // 26)}{chr(65 + i % 26)}X" for i in range(30)]
    codes[:4] = ["USD", "EUR", "GBP", "JPY"]
    lines = ["date,currency,rate,source"]
    for day in range(1, 16):
        for n, code in enumerate(codes):
            lines.append(f"2026-03-{day:02d},{code},{50 + n * 3 + day / 100},RBI")
    wrong = {0: "USD", 5: "GBP", 9: "EUR"}
    for index, line in enumerate(list(lines[1:])):
        _, code, *_ = line.split(",")
        day = int(line[8:10])
        if wrong.get(day - 1) == code:
            lines[index + 1] = f"2026-03-{day:02d},{code},999.99,RBI"
    return ("\n".join(lines) + "\n").encode()


def test_uploading_the_sample_file_stores_450_rates_and_flags_the_three_wrong_ones(
    client: TestClient, db_session: Session
) -> None:
    body = client.post(
        "/api/v1/fx-rates/upload",
        headers=_headers(db_session, "ANALYST"),
        files={"file": ("fx_rates_20260301_20260315.csv", _sample_fx_file(), "text/csv")},
    ).json()
    assert (body["inserted"], body["rejected_count"], body["suspect_count"]) == (450, 0, 3)
    assert all("999.99" in s for s in body["suspect_sample"])
    assert db_session.query(FxRate).filter_by(suspect=True).count() == 3


def test_a_second_upload_skips_existing_rates_instead_of_replacing_them(
    client: TestClient, db_session: Session
) -> None:
    h = _headers(db_session, "ANALYST")
    csv = b"date,currency,rate,source\n2026-03-15,GBP,83.47,RBI\n"
    first = client.post("/api/v1/fx-rates/upload", headers=h, files={"file": ("a.csv", csv)})
    changed = b"date,currency,rate,source\n2026-03-15,GBP,99.00,RBI\n"
    second = client.post("/api/v1/fx-rates/upload", headers=h, files={"file": ("b.csv", changed)})
    assert (
        first.json()["inserted"],
        second.json()["inserted"],
        second.json()["skipped_existing"],
    ) == (1, 0, 1)
    assert db_session.query(FxRate).one().rate_inr == Decimal("83.47000000")


def test_the_upload_is_audited_and_rejects_bad_files(
    client: TestClient, db_session: Session
) -> None:
    h = _headers(db_session, "ANALYST")
    ok = client.post(
        "/api/v1/fx-rates/upload",
        headers=h,
        files={"file": ("a.csv", b"date,currency,rate\n2026-03-15,GBP,83.47\n")},
    )
    assert ok.status_code == 201
    entry = db_session.scalars(select(AuditLog).where(AuditLog.chain_id == INGESTION_CHAIN)).one()
    assert entry.affected_records == {"kind": "fx_rates"}

    for payload, status in [
        (b"date,rate\n2026-03-15,1\n", 422),
        (b"date,currency,rate\n", 422),
        (b"\xff\xfe\x00", 422),
    ]:
        assert (
            client.post(
                "/api/v1/fx-rates/upload", headers=h, files={"file": ("x.csv", payload)}
            ).status_code
            == status
        )


def test_a_viewer_can_list_but_not_upload(client: TestClient, db_session: Session) -> None:
    viewer = _headers(db_session, "VIEWER")
    assert (
        client.post(
            "/api/v1/fx-rates/upload", headers=viewer, files={"file": ("a.csv", b"x")}
        ).status_code
        == 403
    )
    _rate(db_session, "GBP", "83.47")
    _rate(db_session, "USD", "83.10")
    listed = client.get("/api/v1/fx-rates?currency=gbp", headers=viewer).json()
    assert [r["currency"] for r in listed["items"]] == ["GBP"]
    assert client.get("/api/v1/fx-rates?suspect=true", headers=viewer).json()["total"] == 0
    assert client.get("/api/v1/fx-rates?date_from=2026-03-16", headers=viewer).json()["total"] == 0


def test_the_transaction_model_has_the_columns_the_strategies_rely_on() -> None:
    assert {"original_reference_text", "counterparty_name_normalised", "settlement_date"} <= set(
        NormalisedTransaction.__table__.columns.keys()
    )
