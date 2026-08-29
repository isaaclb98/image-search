"""
tests/test_retry.py — retry-with-backoff helper used by the indexer.

The indexer's batch upsert is wrapped in a retry that backs off
exponentially (2s, 4s, 8s) before giving up. The retry helper is
intentionally minimal and dependency-free so it's easy to test in
isolation; the indexer wires it around its single upsert call.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from indexer.retry import RetryExhausted, retry_with_backoff


def test_returns_first_success_without_retry():
    calls = []

    def op():
        calls.append(1)
        return "ok"

    result = retry_with_backoff(op, max_attempts=3, base_delay_s=0.0, sleep=lambda _s: None)
    assert result == "ok"
    assert len(calls) == 1


def test_retries_until_success():
    calls = []

    def op():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError(f"fail {len(calls)}")
        return "ok"

    result = retry_with_backoff(op, max_attempts=3, base_delay_s=0.0, sleep=lambda _s: None)
    assert result == "ok"
    assert len(calls) == 3


def test_raises_retry_exhausted_after_max_attempts():
    calls = []

    def op():
        calls.append(1)
        raise RuntimeError("nope")

    with pytest.raises(RetryExhausted) as exc_info:
        retry_with_backoff(op, max_attempts=3, base_delay_s=0.0, sleep=lambda _s: None)
    assert "nope" in str(exc_info.value)
    assert len(calls) == 3


def test_exponential_backoff_delays():
    """Delays must double each attempt: 2s, 4s, 8s for base=2, attempts=4."""
    recorded_delays = []

    def fake_sleep(seconds):
        recorded_delays.append(seconds)

    def always_fail():
        raise RuntimeError("x")

    with pytest.raises(RetryExhausted):
        retry_with_backoff(always_fail, max_attempts=4, base_delay_s=2.0, sleep=fake_sleep)

    # 3 retries → 3 delays (between attempts): 2, 4, 8.
    assert recorded_delays == [2.0, 4.0, 8.0]


def test_sleep_is_skipped_on_last_attempt():
    """No sleep after the final failed attempt — caller should fail fast."""
    recorded_delays = []

    def fake_sleep(seconds):
        recorded_delays.append(seconds)

    def always_fail():
        raise RuntimeError("x")

    with pytest.raises(RetryExhausted):
        retry_with_backoff(always_fail, max_attempts=3, base_delay_s=1.0, sleep=fake_sleep)

    # 3 attempts → 2 sleeps (between attempts 1→2 and 2→3).
    assert recorded_delays == [1.0, 2.0]


def test_propagates_only_last_exception_in_message():
    """The RetryExhausted message must reference the final failure, not earlier ones."""

    def op():
        raise ValueError("first")
    # After retries, ValueError should be the wrapped cause.

    def op2():
        raise ValueError("final")

    # Simulate the failure pattern: op raises once, op2 raises once.
    seq = iter([op, op2])
    attempt = {"n": 0}

    def pick_op():
        attempt["n"] += 1
        if attempt["n"] == 1:
            raise ValueError("first")
        raise ValueError("final")

    with pytest.raises(RetryExhausted) as exc_info:
        retry_with_backoff(pick_op, max_attempts=3, base_delay_s=0.0, sleep=lambda _s: None)
    assert "final" in str(exc_info.value)
    assert "first" not in str(exc_info.value)


def test_uses_real_sleep_when_none_provided(monkeypatch):
    """Default sleep must use time.sleep so delays actually elapse in prod.
    In this test we monkeypatch time.sleep to verify it's invoked without hanging."""
    slept = []

    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    def always_fail():
        raise RuntimeError("x")

    with pytest.raises(RetryExhausted):
        retry_with_backoff(always_fail, max_attempts=3, base_delay_s=0.5)
    assert slept == [0.5, 1.0]
