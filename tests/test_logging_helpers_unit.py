"""tests/test_logging_helpers_unit.py — unit tests for search._logging.

The structured-logging helpers attach their data via the
`extra=` kwarg. Default log formatters don't print the
extra fields, so the visible output stays human-readable.
These tests pin both the message AND the structured fields
so a future formatter swap (e.g. JSON) is correctly
expected to surface the structured data.
"""

from __future__ import annotations

import logging

from search._logging import (
    log_qdrant_error,
    log_qdrant_timeout,
    log_request_completed,
)


def test_log_qdrant_error_attaches_structured_fields() -> None:
    logger = logging.getLogger("test.qdrant_error")
    with _capture(logger) as records:
        log_qdrant_error(
            logger,
            endpoint="/api/search",
            error=ConnectionError("qdrant is down"),
            op="query_points",
            extra={"qdrant_collection": "images"},
        )
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.WARNING
    assert "Qdrant query_points failed" in rec.getMessage()
    # The default formatter would not print these — but they
    # are present on the record for a JSON formatter to pick up.
    assert rec.qdrant_op == "query_points"
    assert rec.qdrant_endpoint == "/api/search"
    assert rec.qdrant_error_type == "ConnectionError"
    assert rec.qdrant_error_msg == "qdrant is down"
    # extra= passes through
    assert rec.qdrant_collection == "images"


def test_log_qdrant_timeout_includes_budget() -> None:
    logger = logging.getLogger("test.qdrant_timeout")
    with _capture(logger) as records:
        log_qdrant_timeout(
            logger,
            endpoint="/api/centroids/kpop/search",
            op="recommend",
            timeout_ms=60_000,
        )
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.WARNING
    assert rec.qdrant_op == "recommend"
    assert rec.qdrant_endpoint == "/api/centroids/kpop/search"
    assert rec.qdrant_timeout_ms == 60_000


def test_log_request_completed_is_debug_level() -> None:
    """Search responses are too chatty at INFO — debug only."""
    logger = logging.getLogger("test.request_completed")
    with _capture(logger) as records:
        log_request_completed(
            logger,
            endpoint="/api/search",
            took_ms=120,
            result_count=24,
            has_more=True,
        )
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.DEBUG
    assert rec.endpoint == "/api/search"
    assert rec.took_ms == 120
    assert rec.result_count == 24
    assert rec.has_more is True


def test_log_qdrant_error_extra_is_optional() -> None:
    """`extra=None` should be safe (no key error)."""
    logger = logging.getLogger("test.qdrant_error_no_extra")
    with _capture(logger) as records:
        log_qdrant_error(
            logger, endpoint="/api/x", error=OSError("boom"), op="search",
            extra=None,
        )
    rec = records[0]
    assert rec.qdrant_op == "search"


# ---------- helpers ----------

from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def _capture(logger: logging.Logger) -> Iterator[list[logging.LogRecord]]:
    """Capture all log records emitted on `logger` for the duration of the block."""
    records: list[logging.LogRecord] = []

    class _Sink(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Sink(level=logging.DEBUG)
    logger.addHandler(handler)
    # ensure propagation is on so we don't double-capture
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
