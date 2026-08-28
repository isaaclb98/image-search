"""
tests/test_middleware_unit.py — Unit tests for search/middleware.py.

The request-timing middleware attaches structured data to each log
record via `extra={"request_log": ...}` for JSON formatters. The
human-readable message is a compact fallback string.

We invoke the middleware via its `dispatch(request, call_next)` method
directly — no FastAPI app needed. `call_next` is the downstream handler
that the middleware wraps.
"""
from __future__ import annotations

import asyncio
import logging

import pytest
from starlette.requests import Request
from starlette.responses import Response

from search.middleware import (
    RequestTimingMiddleware,
    SLOW_REQUEST_THRESHOLD_MS,
)


class _CollectHandler(logging.Handler):
    """Capture log records for assertion."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def captured_logs():
    """Attach a collector to the middleware logger."""
    handler = _CollectHandler()
    logger = logging.getLogger("search.middleware")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)


def _request(scope_path="/test", method="GET", headers=None):
    """Build a Starlette Request with the given path/method/headers."""
    headers = headers or {}
    scope = {
        "type": "http",
        "method": method,
        "path": scope_path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("client", 12345),
    }
    return Request(scope)


async def _invoke(middleware, request, *, status_code=200, raise_exc=False):
    """Invoke middleware.dispatch(request, call_next)."""
    async def call_next(req):
        if raise_exc:
            raise RuntimeError("downstream boom")
        return Response(status_code=status_code)
    return await middleware.dispatch(request, call_next)


# ----- Structured log payload -----

class TestStructuredLogPayload:
    """The middleware attaches a structured dict to each log record."""

    def test_log_record_has_request_log_extra(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request()

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        assert len(captured_logs.records) == 1
        rec = captured_logs.records[0]
        assert hasattr(rec, "request_log"), "middleware must attach request_log via extra="
        assert isinstance(rec.request_log, dict)

    def test_payload_has_all_required_fields(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request(scope_path="/api/photo/abc", method="GET")

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        log = captured_logs.records[0].request_log
        assert "ts" in log
        assert "method" in log
        assert "path" in log
        assert "status" in log
        assert "duration_ms" in log

    def test_payload_method_path_status_correct(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request(scope_path="/api/photo/abc", method="POST")

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request, status_code=404)

        asyncio.run(runner())

        log = captured_logs.records[0].request_log
        assert log["method"] == "POST"
        assert log["path"] == "/api/photo/abc"
        assert log["status"] == 404

    def test_timestamp_is_iso8601_utc(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request()

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        ts = captured_logs.records[0].request_log["ts"]
        # ISO-8601 UTC: contains 'T', ends with 'Z' or '+00:00'
        assert "T" in ts
        assert ts.endswith(("Z", "+00:00"))

    def test_duration_ms_is_numeric_and_nonneg(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request()

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        d = captured_logs.records[0].request_log["duration_ms"]
        assert isinstance(d, (int, float))
        assert d >= 0


# ----- Human-readable message -----

class TestHumanReadableMessage:
    """The log message is a compact 'METHOD path -> STATUS in DURATIONms' string."""

    def test_message_contains_method_path_status_duration(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request(scope_path="/foo", method="GET")

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        msg = captured_logs.records[0].getMessage()
        assert "GET" in msg
        assert "/foo" in msg
        assert "200" in msg
        assert "ms" in msg


# ----- Slow request handling -----

class TestSlowRequestLogging:
    """Requests > 1s are logged at WARN; fast requests at INFO."""

    def test_fast_request_logs_at_info(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request()

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        assert captured_logs.records[0].levelno == logging.INFO

    def test_slow_request_logs_at_warn(self, captured_logs, monkeypatch):
        """Inflate perf_counter so the second call is 1.5s after the first."""
        import search.middleware as mw_module
        original_perf = mw_module.time.perf_counter
        counter = {"n": 0}
        def slow_perf():
            counter["n"] += 1
            # First call (t0): real value. Second call (elapsed): +1.5s.
            if counter["n"] == 1:
                return original_perf()
            return original_perf() + 1.5
        monkeypatch.setattr(mw_module.time, "perf_counter", slow_perf)

        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request()

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        rec = captured_logs.records[0]
        assert rec.levelno == logging.WARNING, (
            f"slow request should be WARN, got {rec.levelname}: "
            f"duration_ms={rec.request_log.get('duration_ms')}"
        )

    def test_slow_threshold_constant(self):
        assert SLOW_REQUEST_THRESHOLD_MS == 1000.0

    def test_just_under_threshold_is_info(self, captured_logs, monkeypatch):
        """Duration just under 1000ms should still be INFO."""
        import search.middleware as mw_module
        original_perf = mw_module.time.perf_counter
        counter = {"n": 0}
        def fake_perf():
            counter["n"] += 1
            if counter["n"] == 1:
                return original_perf()
            return original_perf() + 0.999  # 999ms
        monkeypatch.setattr(mw_module.time, "perf_counter", fake_perf)

        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request()

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        assert captured_logs.records[0].levelno == logging.INFO


# ----- Error handling -----

class TestErrorHandling:
    """The middleware must log even when the downstream app raises."""

    def test_exception_still_emits_log_with_500(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request()

        async def runner():
            captured_logs.records.clear()
            with pytest.raises(RuntimeError):
                await _invoke(middleware, request, raise_exc=True)

        asyncio.run(runner())

        assert len(captured_logs.records) == 1
        log = captured_logs.records[0].request_log
        # status defaults to 500 when response never sent
        assert log["status"] == 500

    def test_500_response_logged(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request()

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request, status_code=500)

        asyncio.run(runner())

        assert captured_logs.records[0].request_log["status"] == 500


# ----- Request ID propagation -----

class TestRequestIdPropagation:
    """X-Request-ID header is preserved in the structured payload."""

    def test_request_id_from_header(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request(headers={"X-Request-ID": "abc-123-def"})

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        assert captured_logs.records[0].request_log.get("request_id") == "abc-123-def"

    def test_no_request_id_header_means_no_field(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request()

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        log = captured_logs.records[0].request_log
        assert log.get("request_id") is None

    def test_request_id_case_insensitive(self, captured_logs):
        """HTTP headers are case-insensitive."""
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request(headers={"x-request-id": "lower-case-id"})

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        assert captured_logs.records[0].request_log.get("request_id") == "lower-case-id"


# ----- Exactly one log per request -----

class TestExactlyOneLogPerRequest:
    """No double-logging on success or error."""

    def test_success_emits_exactly_one_log(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request()

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        assert len(captured_logs.records) == 1

    def test_error_emits_exactly_one_log(self, captured_logs):
        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request()

        async def runner():
            captured_logs.records.clear()
            with pytest.raises(RuntimeError):
                await _invoke(middleware, request, raise_exc=True)

        asyncio.run(runner())

        assert len(captured_logs.records) == 1


# ----- Threshold boundary -----

class TestThresholdBoundary:
    """Verify the slow/fast cutoff at exactly 1000ms."""

    def test_exactly_over_threshold_is_warn(self, captured_logs, monkeypatch):
        """1001ms is over the 1000ms threshold → WARN."""
        import search.middleware as mw_module
        original_perf = mw_module.time.perf_counter
        counter = {"n": 0}
        def fake_perf():
            counter["n"] += 1
            if counter["n"] == 1:
                return original_perf()
            return original_perf() + 1.001
        monkeypatch.setattr(mw_module.time, "perf_counter", fake_perf)

        captured_logs.records.clear()
        middleware = RequestTimingMiddleware(_dummy_app)
        request = _request()

        async def runner():
            captured_logs.records.clear()
            await _invoke(middleware, request)

        asyncio.run(runner())

        assert captured_logs.records[0].levelno == logging.WARNING


# ----- Module-level -----

async def _dummy_app(scope, receive, send):
    """Trivial ASGI app for RequestTimingMiddleware's constructor."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b""})