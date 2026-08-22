"""
search/middleware.py — request timing + structured access logs (§C6).

A FastAPI middleware that:

- Records the request duration.
- Emits a structured JSON log line per request with:
    - timestamp (ISO-8601 UTC)
    - method
    - path
    - status_code
    - duration_ms (float)
    - request_id (optional, from `X-Request-ID` header)
- Tags slow requests (>1s) at WARN; normal requests at INFO.

§4.10 of `docs/backend-refactor-plan.md` calls for the same shape; this
module exists so the application can opt in via `app.add_middleware(...)`
without coupling route handlers to logging concerns.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

__all__ = ["RequestTimingMiddleware"]

logger = logging.getLogger(__name__)

# Requests slower than this are logged at WARN; faster requests at INFO.
SLOW_REQUEST_THRESHOLD_MS = 1000.0


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Emit one structured log line per request with timing + status.

    The log format is stable: a JSON object with the fields named in
    §4.13 (logging conventions). Tests in `tests/test_middleware.py`
    pin the field set.
    """

    async def dispatch(self, request: Request, call_next):
        t0 = time.perf_counter()
        status_code = 500  # default; overwritten on a successful response
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            self._log_request(
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
                request_id=request.headers.get("x-request-id"),
            )

    @staticmethod
    def _log_request(
        *,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        request_id: str | None,
    ) -> None:
        level = logging.WARNING if duration_ms >= SLOW_REQUEST_THRESHOLD_MS else logging.INFO
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "path": path,
            "status": status_code,
            "duration_ms": round(duration_ms, 3),
        }
        if request_id is not None:
            record["request_id"] = request_id
        # Use the `extra` kwarg so structured consumers (e.g. JSON
        # formatters) can pick the dict up; the message is a
        # compact fallback for human readers.
        logger.log(
            level,
            "%s %s -> %d in %.1fms",
            method, path, status_code, duration_ms,
            extra={"request_log": record},
        )
