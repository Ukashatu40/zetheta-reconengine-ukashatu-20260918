# src/recon/api/main.py
"""FastAPI application: request-id middleware, RFC 7807 error handlers,
health endpoints (unauthenticated) and the versioned API routers."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from recon import cache as cache_module
from recon.api.deps import get_db
from recon.api.errors import problem_response, request_id_of
from recon.api.ratelimit import rate_limit_middleware
from recon.api.v1 import (
    admin,
    anomalies,
    audit,
    dashboard,
    exceptions,
    files,
    fx,
    matches,
    me,
    reports,
    runs,
    upload,
)

logger = logging.getLogger("recon.api")
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")

app = FastAPI(
    title="Automated Reconciliation Engine",
    description="Zetheta Algorithms — Automated Reconciliation Engine for Multi-Bank Settlement",
    version="0.1.0",
)


# Registered before the request-id middleware so the request id is already set
# when a rate-limit problem response is built (the last one added runs first).
app.middleware("http")(rate_limit_middleware)


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    incoming = request.headers.get("X-Request-ID", "")
    request_id = incoming if _REQUEST_ID_PATTERN.fullmatch(incoming) else str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    headers = dict(exc.headers) if exc.headers else None
    return problem_response(request, exc.status_code, str(exc.detail), headers=headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> Response:
    errors = [
        {"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()
    ]  # no "input": it may be sensitive
    return problem_response(request, 422, "request validation failed", extra={"errors": errors})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, _exc: Exception) -> Response:
    logger.exception("unhandled error, request_id=%s", request_id_of(request))
    return problem_response(request, 500, "internal server error")


@app.get("/health/live")
def health_live() -> dict[str, str]:
    """Liveness: process is running and serving requests."""
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready(
    session: Annotated[Session, Depends(get_db)], response: Response
) -> dict[str, object]:
    """Readiness: the database must answer. Redis is optional (the cache and
    limiter fail open), so its state is reported but never makes us unready."""
    checks: dict[str, str] = {}
    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.exception("readiness: database check failed")
        checks["database"] = "down"
    if cache_module.redis_configured():
        checks["redis"] = "ok" if cache_module.get_cache().ping() else "down"
    else:
        checks["redis"] = "not_configured"
    ready = checks["database"] == "ok"
    if not ready:
        response.status_code = 503
    return {"status": "ok" if ready else "unavailable", "checks": checks}


app.include_router(me.router)
app.include_router(exceptions.router)
app.include_router(audit.router)
app.include_router(dashboard.router)
app.include_router(runs.router)
app.include_router(upload.router)
app.include_router(matches.router)
app.include_router(fx.router)
app.include_router(anomalies.router)
app.include_router(files.router)
app.include_router(admin.router)
app.include_router(reports.router)
