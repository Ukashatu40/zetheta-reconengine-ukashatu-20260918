# src/recon/api/main.py
"""Minimal FastAPI entrypoint.

This is a placeholder sufficient to prove the container, health checks and
compose networking work end to end. The real router structure (recon.api.v1.*)
lands in WP5.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(
    title="Automated Reconciliation Engine",
    description="Zetheta Algorithms — Automated Reconciliation Engine for Multi-Bank Settlement",
    version="0.1.0",
)


@app.get("/health/live")
def health_live() -> dict[str, str]:
    """Liveness: process is running and serving requests."""
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready() -> dict[str, str]:
    """Readiness: placeholder for now.

    WP2 onward this checks DB and Redis connectivity before returning ok.
    """
    return {"status": "ok"}
