"""
search/_logging.py — structured-logging helpers for the search package.

The codebase uses the standard `logger.info(msg, *args)` style
everywhere, which embeds the structured data in the message
string. That makes the data hard to query on later (grepping
for a Qdrant timeout from a particular collection requires
parsing free-form text) and means we can't ship a JSON
formatter without losing the structured fields.

This module provides a thin wrapper that attaches the
structured data via the `extra=` argument. Existing
`basicConfig` callers see the same human-readable output
(the `extra` keys are appended to the record by
`LogRecord.__dict__` but only printed if the formatter
references them — which the default `%(message)s` format
does NOT, so the message looks the same as before).

When a future operator wants a JSON log stream they can
swap the formatter for one that reads `record.__dict__['x']`
and the structured fields become first-class.

Conventions:

- All helpers take the logger as the first argument so the
  call site looks like `log_qdrant_error(logger, "search",
  collection="kpop", error=e)`. The logger module is part
  of the public API of every helper — don't bury it in a
  closure.

- The `extra=` dict uses snake_case keys, namespace-prefixed
  by domain (e.g. `qdrant_collection`, `qdrant_point_count`)
  so a JSON formatter can group fields. Names that don't
  belong to a domain are top-level (`endpoint`, `took_ms`).

- The message string stays human-readable: when the default
  text formatter is in use, operators still see a clean
  one-line message they can scan in `docker logs`.
"""

from __future__ import annotations

import logging
from typing import Any


def log_qdrant_error(
    logger: logging.Logger,
    *,
    endpoint: str,
    error: BaseException,
    op: str = "search",
    extra: dict[str, Any] | None = None,
) -> None:
    """Log a Qdrant call failure with structured `extra` fields.

    The default log line reads
    `WARNING  search.qdrant_client: Qdrant <op> failed for <endpoint>`.
    The structured fields (op, endpoint, qdrant_error_type,
    qdrant_error_msg) are attached via `extra=` for any
    formatter that wants them.

    `op` is the operation name (e.g. "query_points",
    "recommend", "scroll") — NOT the HTTP method, since
    Qdrant can be called via gRPC or REST and the
    operation name is the stable identifier.

    `endpoint` is the caller's endpoint (e.g. "/api/search")
    so a log search can group Qdrant failures by which
    user-facing feature triggered them.
    """
    fields: dict[str, Any] = {
        "qdrant_op": op,
        "qdrant_endpoint": endpoint,
        "qdrant_error_type": type(error).__name__,
        "qdrant_error_msg": str(error),
    }
    if extra:
        fields.update(extra)
    logger.warning(
        "Qdrant %s failed for %s: %s",
        op, endpoint, error,
        extra=fields,
    )


def log_qdrant_timeout(
    logger: logging.Logger,
    *,
    endpoint: str,
    op: str,
    timeout_ms: int,
    extra: dict[str, Any] | None = None,
) -> None:
    """Log a Qdrant timeout with the configured budget as structured data.

    The `qdrant_timeout_ms` field is the configured budget so a
    later operator can see "we timed out at 60s on a search
    that was budgeted for 60s" without parsing the message.
    """
    fields: dict[str, Any] = {
        "qdrant_op": op,
        "qdrant_endpoint": endpoint,
        "qdrant_timeout_ms": timeout_ms,
    }
    if extra:
        fields.update(extra)
    logger.warning(
        "Qdrant %s timeout for %s after %dms",
        op, endpoint, timeout_ms,
        extra=fields,
    )


def log_request_completed(
    logger: logging.Logger,
    *,
    endpoint: str,
    took_ms: int,
    result_count: int,
    has_more: bool = False,
    extra: dict[str, Any] | None = None,
) -> None:
    """Log a successful request with timing + result-count structured fields.

    Currently DEBUG level — search responses are too chatty
    at INFO. Operators turn this up when investigating a
    specific latency report.
    """
    fields: dict[str, Any] = {
        "endpoint": endpoint,
        "took_ms": took_ms,
        "result_count": result_count,
        "has_more": has_more,
    }
    if extra:
        fields.update(extra)
    logger.debug(
        "%s completed in %dms (%d results, has_more=%s)",
        endpoint, took_ms, result_count, has_more,
        extra=fields,
    )
