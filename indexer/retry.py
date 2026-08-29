"""
indexer/retry.py — minimal retry-with-exponential-backoff helper.

Used by the indexer to wrap batch upserts in a bounded retry so a
single transient Qdrant failure (5xx, network blip) doesn't abort
the whole indexing job. Per-file errors are NOT retried here — the
indexer already logs and skips those.

Contract:
    retry_with_backoff(op, max_attempts=3, base_delay_s=2.0)

- Up to `max_attempts` total invocations of `op`.
- Between attempts, sleeps for base * 2^(n-1) seconds (2s, 4s, 8s for
  base=2 with max_attempts=4).
- If all attempts fail, raises RetryExhausted wrapping the LAST
  exception (earlier failures are intentionally dropped — the message
  references the final one, not the chain).
- No sleep after the final failed attempt.
- `sleep` is injectable for tests; defaults to time.sleep.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class RetryExhausted(Exception):
    """Raised when every attempt of `op` failed."""

    def __init__(self, attempts: int, last_exc: BaseException) -> None:
        self.attempts = attempts
        self.last_exc = last_exc
        super().__init__(
            f"Operation failed after {attempts} attempt(s); "
            f"last error: {type(last_exc).__name__}: {last_exc}"
        )


def retry_with_backoff(
    op: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay_s: float = 2.0,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Run `op` with exponential backoff between attempts.

    Args:
        op: zero-arg callable; its return value is returned on success.
        max_attempts: total invocation count, including the first try.
        base_delay_s: delay before the SECOND attempt; subsequent
            delays double. Last sleep is between attempt N-1 and N
            when N == max_attempts — there is no sleep after the
            final failure.
        sleep: injectable sleep callable (for tests). Defaults to
            time.sleep.

    Returns:
        Whatever `op` returns on its first successful attempt.

    Raises:
        RetryExhausted: when every attempt failed. Wraps the last
            exception's type and message.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    _sleep = sleep if sleep is not None else time.sleep
    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return op()
        except BaseException as exc:  # noqa: BLE001 — by design: any exception is retryable
            last_exc = exc
            if attempt == max_attempts:
                break
            _sleep(base_delay_s * (2 ** (attempt - 1)))

    assert last_exc is not None  # always true when we reach here
    raise RetryExhausted(max_attempts, last_exc)
