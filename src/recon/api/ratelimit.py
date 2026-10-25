# src/recon/api/ratelimit.py
"""Fixed-window rate limiting for /api routes (R76).

The identity is a hash of the presented API key (so a key is never stored or
logged), or the client address when none is presented. Uploads have a lower
limit than everything else. The limiter fails open: if the counter backend is
down, requests are allowed rather than the API being taken offline with it.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from recon import cache as cache_module
from recon.api.errors import problem_response

_WINDOW_SECONDS = 60
_UPLOAD_PATHS = ("/api/v1/files/upload", "/api/v1/upload")


def _limit_for(path: str) -> tuple[str, int]:
    if path in _UPLOAD_PATHS:
        return "upload", int(os.environ.get("RATE_LIMIT_UPLOAD_PER_MINUTE", "10"))
    return "api", int(os.environ.get("RATE_LIMIT_PER_MINUTE", "120"))


def _identity(request: Request) -> str:
    key = request.headers.get("X-API-Key")
    if key:
        return "key:" + hashlib.sha256(key.encode()).hexdigest()[:16]
    return "ip:" + (request.client.host if request.client else "unknown")


async def rate_limit_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    scope, limit = _limit_for(path)
    now = time.time()
    window = int(now // _WINDOW_SECONDS)
    count = cache_module.get_cache().incr_window(
        f"rl:{scope}:{_identity(request)}:{window}", _WINDOW_SECONDS
    )
    retry_after = max(1, int((window + 1) * _WINDOW_SECONDS - now))
    if limit > 0 and count > limit:
        return problem_response(
            request,
            429,
            "rate limit exceeded",
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
            },
        )
    response = await call_next(request)
    if count:
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - count))
    return response
