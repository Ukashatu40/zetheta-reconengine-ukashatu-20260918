"""The full run with the rule stage: split, netted and cross-currency groups are
matched, counted in the run metrics, and the match rate reflects them."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from recon.api.deps import get_clock, get_db
from recon.api.main import app
from recon.persistence.models import ApiKey, FxRate, MatchResult
from recon.security.api_keys import generate_api_key, hash_api_key
from tests.integration.factories import make_ingestion_file, make_txn

_DAY = date(2026, 3, 15)
_NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)


def _clock() -> Callable[[], datetime]:
    return lambda: _NOW


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_clock] = _clock
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def analyst(db_session: Session) -> dict[str, str]:
    raw = generate_api_key()
    db_session.add(
        ApiKey(key_hash=hash_api_key(raw), name=f"a-{uuid.uuid4().hex[:6]}", role="ANALYST")
    )
    db_session.flush()
    return {"X-API-Key": raw}


def _txn(session: Session, source: str, ref: str, minor: int, currency: str = "INR") -> None:
    f = make_ingestion_file(session)
    session.add(
        make_txn(
            f.id,
            source=source,
            txn_date=_DAY,
            txn_id=ref,
            original_reference_text=ref,
            normalised_reference="".join(c for c in ref.upper() if c.isalnum()),
            amount=Decimal(minor) / 100,
            amount_minor=minor,
            currency=currency,
        )
    )


def _reconcile(client: TestClient, headers: dict[str, str]) -> Any:
    return client.post(
        "/api/v1/reconciliation/run",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json={"bank_code": "HDFC"},
    ).json()


def test_a_run_matches_exact_split_netted_and_cross_currency_groups(
    client: TestClient, db_session: Session, analyst: dict[str, str]
) -> None:
    db_session.add(FxRate(rate_date=_DAY, currency="GBP", rate_inr=Decimal("83.47"), source="RBI"))
    _txn(db_session, "INTERNAL", "EXACT1", 500_000)  # 1 exact pair
    _txn(db_session, "EXTERNAL", "EXACT1", 500_000)
    _txn(db_session, "INTERNAL", "PAY-100", 100_000)  # one split group: 1 internal, 2 externals
    _txn(db_session, "EXTERNAL", "PAY-100-1", 40_000)
    _txn(db_session, "EXTERNAL", "PAY-100-2", 60_000)
    _txn(db_session, "EXTERNAL", "NET-20260315", 14_285_714, "GBP")  # B4.2: GBP vs 23 INR
    base, extra = divmod(1_199_999_976, 23)
    for i in range(1, 24):
        _txn(db_session, "INTERNAL", f"NET-20260315-{i:03d}", base + (1 if i <= extra else 0))
    _txn(db_session, "EXTERNAL", "LONELY", 1_234)  # nothing matches this one
    db_session.flush()

    run = _reconcile(client, analyst)

    metrics = run["metrics"]
    assert run["status"] == "COMPLETED", run["error_summary"]
    assert metrics["exact_matched"] == 1
    assert (metrics["split_groups"], metrics["netted_groups"]) == (1, 1)
    # externals: 1 exact + 2 split parts + 1 GBP netted entry matched, 1 left over
    assert metrics["externals_matched"] == 4
    assert metrics["overall_match_rate_of_total"] == pytest.approx(4 / 5)
    assert metrics["duration_seconds"]["rules"] >= 0
    types = sorted(
        db_session.scalars(select(MatchResult.match_type).where(MatchResult.run_id == run["id"]))
    )
    assert types.count("EXACT") == 1 and types.count("SPLIT") == 2 and types.count("NETTED") == 23


def test_fast_mode_skips_the_rule_stage(
    client: TestClient, db_session: Session, analyst: dict[str, str]
) -> None:
    _txn(db_session, "INTERNAL", "PAY-100", 100_000)
    _txn(db_session, "EXTERNAL", "PAY-100-1", 40_000)
    _txn(db_session, "EXTERNAL", "PAY-100-2", 60_000)
    db_session.flush()
    fast = client.post(
        "/api/v1/reconciliation/run",
        headers={**analyst, "Idempotency-Key": uuid.uuid4().hex},
        json={"bank_code": "HDFC", "mode": "FAST"},
    ).json()
    assert fast["metrics"]["split_groups"] == 0
    assert db_session.query(MatchResult).count() == 0
