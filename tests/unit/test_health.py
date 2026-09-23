# tests/unit/test_health.py
"""Smoke tests for the placeholder health endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from recon.api.main import app

client = TestClient(app)


def test_health_live_returns_ok() -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_returns_ok() -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
