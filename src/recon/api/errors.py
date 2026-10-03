# src/recon/api/errors.py
"""RFC 7807 problem responses. Detail strings never include request
bodies, SQL, stack traces or credentials; the request_id lets an operator
find the full detail in the logs."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def problem_response(
    request: Request,
    status_code: int,
    detail: str,
    *,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": HTTPStatus(status_code).phrase,
        "status": status_code,
        "detail": detail,
        "request_id": request_id_of(request),
    }
    if extra:
        body.update(extra)
    return JSONResponse(
        body, status_code=status_code, headers=headers, media_type="application/problem+json"
    )
