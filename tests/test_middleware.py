"""
tests/test_middleware.py — request timing middleware contract (§C6).

Pins:
  - Every request emits exactly one structured log line.
  - The log line carries the documented fields (§4.13):
    ts, method, path, status, duration_ms, plus optional request_id.
  - Requests slower than `SLOW_REQUEST_THRESHOLD_MS` log at WARNING;
    faster requests log at INFO.
  - The middleware captures status code correctly even when a
    downstream handler raises (the log line still records status 500).
"""

from __future__ import annotations

import json
import logging
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_middleware():
    from search.middleware import RequestTimingMiddleware

    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware)

    @app.get("/fast")
    def fast():
        return {"ok": True}

    @app.get("/slow")
    def slow():
        # Sleep a smidge longer than 0ms but well under the slow
        # threshold. Forces `duration_ms > 0` for the assertion below.
        time.sleep(0.001)
        return {"ok": True}

    @app.get("/raise")
    def raise_500():
        raise RuntimeError("boom")

    return app


def _capture_logs(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "search.middleware"]


def test_fast_request_logs_info_with_required_fields(app_with_middleware, caplog):
    """A 200 response emits one INFO log with method, path, status, duration."""
    caplog.set_level(logging.INFO, logger="search.middleware")
    client = TestClient(app_with_middleware)
    resp = client.get("/fast")
    assert resp.status_code == 200

    records = _capture_logs(caplog)
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.INFO
    extra = getattr(rec, "request_log", {})
    assert extra["method"] == "GET"
    assert extra["path"] == "/fast"
    assert extra["status"] == 200
    assert extra["duration_ms"] >= 0
    assert "ts" in extra


def test_request_id_header_propagates_to_log(app_with_middleware, caplog):
    """`X-Request-ID` header value appears as `request_id` in the log."""
    caplog.set_level(logging.INFO, logger="search.middleware")
    client = TestClient(app_with_middleware)
    resp = client.get("/fast", headers={"X-Request-ID": "req-abc-123"})
    assert resp.status_code == 200

    records = _capture_logs(caplog)
    assert len(records) == 1
    extra = getattr(records[0], "request_log", {})
    assert extra.get("request_id") == "req-abc-123"


def test_no_request_id_header_omits_field(app_with_middleware, caplog):
    """No X-Request-ID → no `request_id` key in the log record."""
    caplog.set_level(logging.INFO, logger="search.middleware")
    client = TestClient(app_with_middleware)
    resp = client.get("/fast")
    assert resp.status_code == 200

    records = _capture_logs(caplog)
    extra = getattr(records[0], "request_log", {})
    assert "request_id" not in extra


def test_5xx_response_still_emits_log(app_with_middleware, caplog):
    """A handler that raises still produces one log line with status=500."""
    caplog.set_level(logging.INFO, logger="search.middleware")
    client = TestClient(app_with_middleware, raise_server_exceptions=False)
    resp = client.get("/raise")
    assert resp.status_code == 500

    records = _capture_logs(caplog)
    assert len(records) == 1
    extra = getattr(records[0], "request_log", {})
    assert extra["status"] == 500
    assert extra["path"] == "/raise"


def test_slow_request_logs_warning(app_with_middleware, caplog, monkeypatch):
    """A request slower than `SLOW_REQUEST_THRESHOLD_MS` logs at WARNING."""
    from search import middleware as mw_module

    # Force the threshold to a value the test can hit cheaply.
    monkeypatch.setattr(mw_module, "SLOW_REQUEST_THRESHOLD_MS", 0.0001)

    caplog.set_level(logging.DEBUG, logger="search.middleware")
    client = TestClient(app_with_middleware)
    resp = client.get("/slow")  # sleeps 1ms, exceeds 0.0001ms threshold
    assert resp.status_code == 200

    records = _capture_logs(caplog)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING


def test_each_request_emits_exactly_one_log_line(app_with_middleware, caplog):
    """Two requests produce two log lines, one per request."""
    caplog.set_level(logging.INFO, logger="search.middleware")
    client = TestClient(app_with_middleware)
    client.get("/fast")
    client.get("/fast")
    client.get("/slow")

    records = _capture_logs(caplog)
    assert len(records) == 3
    paths = [getattr(r, "request_log", {}).get("path") for r in records]
    assert paths == ["/fast", "/fast", "/slow"]


def test_duration_ms_is_a_nonnegative_float(app_with_middleware, caplog):
    """`duration_ms` is a float ≥ 0 with sub-millisecond resolution."""
    caplog.set_level(logging.INFO, logger="search.middleware")
    client = TestClient(app_with_middleware)
    client.get("/fast")
    rec = _capture_logs(caplog)[0]
    duration = getattr(rec, "request_log", {})["duration_ms"]
    assert isinstance(duration, float)
    assert duration >= 0.0


def test_middleware_is_a_starlette_base_middleware():
    """`RequestTimingMiddleware` is a Starlette middleware (BaseHTTPMiddleware)."""
    from starlette.middleware.base import BaseHTTPMiddleware

    from search.middleware import RequestTimingMiddleware

    assert issubclass(RequestTimingMiddleware, BaseHTTPMiddleware)
