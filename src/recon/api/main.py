# src/recon/api/main.py
"""FastAPI application: request-id middleware, RFC 7807 error handlers,
health endpoints (unauthenticated) and the versioned API routers."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from recon.api.errors import problem_response, request_id_of
from recon.api.v1 import audit, dashboard, exceptions, runs, upload

logger = logging.getLogger("recon.api")
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")

app = FastAPI(
    title="Automated Reconciliation Engine",
    description="Zetheta Algorithms — Automated Reconciliation Engine for Multi-Bank Settlement",
    version="0.1.0",
)


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
def health_ready() -> dict[str, str]:
    """Readiness: placeholder; a real DB and Redis check comes with the
    operations work."""
    return {"status": "ok"}


app.include_router(exceptions.router)
app.include_router(audit.router)
app.include_router(dashboard.router)
app.include_router(runs.router)
app.include_router(upload.router)
