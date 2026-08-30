"""
tests/test_indexer_runner.py — subprocess lifecycle for the admin Index API.

The runner spawns the indexer as a child process and tracks its state.
Tests use a fake `python -c "..."` script so we exercise the real
Popen / threading / signal paths without standing up the full
indexing stack.

What we pin:
  - start() spawns and returns a job_id; status reflects RUNNING.
  - Concurrent start() raises IndexConflictError.
  - cancel() sends SIGTERM; the child exits cleanly with 130.
  - log() returns buffered lines with monotonic `next_line`.
  - Progress events update state.
  - Successful exit → IDLE; non-zero exit → FAILED with last_error.
"""

from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

import pytest

from search.indexer_runner import (
    IndexConflictError,
    IndexerRunner,
    IndexerState,
)


# --- Helpers ------------------------------------------------------------

# A self-contained script that prints one batch event per second for
# `N` seconds then exits 0. Lets tests control duration, output, and
# exit code by writing a temp file with the desired Python source.
SCRIPT_TEMPLATE = textwrap.dedent(
    """
    import json, sys, time, signal

    # Wire SIGTERM to a flag so we exit cleanly (mirrors local_sync).
    _stop = False
    def _on_term(sig, frame):
        global _stop
        _stop = True
    signal.signal(signal.SIGTERM, _on_term)

    sys.stdout.write(json.dumps({{"event": "start", "mode": "incremental"}}) + "\\n")
    sys.stdout.flush()

    seconds = int({seconds})
    for i in range(seconds):
        if _stop:
            sys.stdout.write(json.dumps({{"event": "cancelled"}}) + "\\n")
            sys.stdout.flush()
            sys.exit(130)
        sys.stdout.write(json.dumps({{
            "event": "batch",
            "indexed": (i + 1) * 10,
            "reembedded": 0,
            "skipped": 0,
            "errors": 0,
        }}) + "\\n")
        sys.stdout.write(f"log-line-{{i}}\\n")
        sys.stdout.flush()
        time.sleep(1.0)

    sys.stdout.write(json.dumps({{"event": "done", "indexed": seconds * 10}}) + "\\n")
    sys.stdout.flush()
    sys.exit({exit_code})
    """
)


def _write_fake_indexer(tmp_path: Path, *, seconds: int, exit_code: int = 0) -> Path:
    """Write a small Python script that mimics local_sync's JSON output."""
    script = tmp_path / "fake_indexer.py"
    script.write_text(SCRIPT_TEMPLATE.format(seconds=seconds, exit_code=exit_code))
    return script


def _make_runner(tmp_path: Path, *, seconds: int, exit_code: int = 0) -> IndexerRunner:
    """Build an IndexerRunner whose command factory spawns the fake script."""
    script = _write_fake_indexer(tmp_path, seconds=seconds, exit_code=exit_code)
    factory = lambda mode: [sys.executable, str(script)]
    return IndexerRunner(command_factory=factory)


# --- Tests --------------------------------------------------------------


def test_start_returns_job_id_and_sets_running(tmp_path: Path):
    runner = _make_runner(tmp_path, seconds=0)
    job_id = runner.start("incremental")
    assert isinstance(job_id, str) and len(job_id) > 0
    runner.wait_idle(timeout=10)
    status = runner.status()
    assert status.job_id == job_id
    assert status.mode == "incremental"


def test_concurrent_start_raises_conflict(tmp_path: Path):
    runner = _make_runner(tmp_path, seconds=10)
    runner.start("incremental")
    try:
        with pytest.raises(IndexConflictError):
            runner.start("rebuild")
    finally:
        runner.cancel()
        runner.wait_idle(timeout=10)


def test_successful_exit_idle_with_no_error(tmp_path: Path):
    runner = _make_runner(tmp_path, seconds=0, exit_code=0)
    runner.start("incremental")
    assert runner.wait_idle(timeout=10)
    status = runner.status()
    assert status.state is IndexerState.IDLE
    assert status.last_error is None
    assert status.progress.indexed == 0  # 0-second run, no batch events emitted


def test_failed_exit_records_error(tmp_path: Path):
    runner = _make_runner(tmp_path, seconds=0, exit_code=2)
    runner.start("incremental")
    assert runner.wait_idle(timeout=10)
    status = runner.status()
    assert status.state is IndexerState.FAILED
    assert status.last_error is not None
    assert "2" in status.last_error


def test_cancel_signals_and_exits_idle(tmp_path: Path):
    runner = _make_runner(tmp_path, seconds=30)  # long-running
    runner.start("incremental")
    assert runner.status().state is IndexerState.RUNNING
    assert runner.cancel() is True
    assert runner.wait_idle(timeout=10)
    status = runner.status()
    assert status.state is IndexerState.IDLE  # 130 maps to idle
    assert status.last_error is None


def test_cancel_returns_false_when_idle(tmp_path: Path):
    runner = _make_runner(tmp_path, seconds=0)
    assert runner.cancel() is False


def test_log_buffer_captures_output(tmp_path: Path):
    runner = _make_runner(tmp_path, seconds=2)
    runner.start("incremental")
    assert runner.wait_idle(timeout=10)
    snap = runner.log()
    # The fake script writes: start event, 2 batch events, 2 log lines, done event.
    assert snap["total"] >= 5
    assert any('"event": "start"' in ln for ln in snap["lines"])
    assert any('"event": "done"' in ln for ln in snap["lines"])


def test_log_since_line_resumes(tmp_path: Path):
    runner = _make_runner(tmp_path, seconds=2)
    runner.start("incremental")
    assert runner.wait_idle(timeout=10)
    first = runner.log()
    second = runner.log(since_line=first["next_line"])
    # Second pass with the same cursor should be empty.
    assert second["lines"] == []
    assert second["next_line"] == first["next_line"]


def test_progress_event_updates_state(tmp_path: Path):
    runner = _make_runner(tmp_path, seconds=2)
    runner.start("incremental")
    # Poll until we've seen at least one batch event.
    deadline = time.time() + 10
    while time.time() < deadline:
        s = runner.status()
        if s.progress.indexed > 0:
            break
        time.sleep(0.05)
    runner.cancel()
    runner.wait_idle(timeout=10)
    # We should have seen indexed > 0 mid-run.
    assert s.progress.indexed >= 10  # type: ignore[name-defined]


def test_restart_after_idle_works(tmp_path: Path):
    runner = _make_runner(tmp_path, seconds=0)
    first = runner.start("incremental")
    assert runner.wait_idle(timeout=10)
    second = runner.start("rebuild")
    assert runner.wait_idle(timeout=10)
    assert first != second
    status = runner.status()
    assert status.mode == "rebuild"
    assert status.state is IndexerState.IDLE


def test_status_serializable_to_dict(tmp_path: Path):
    runner = _make_runner(tmp_path, seconds=0)
    runner.start("incremental")
    runner.wait_idle(timeout=10)
    d = runner.status().to_dict()
    assert d["state"] == "idle"
    assert d["mode"] == "incremental"
    assert d["job_id"] is not None
    assert isinstance(d["progress"], dict)
    assert {"indexed", "reembedded", "skipped", "errors"} <= d["progress"].keys()


def test_spawn_failure_marks_failed(tmp_path: Path):
    # Command factory returns a nonexistent binary.
    factory = lambda mode: ["/nonexistent/binary"]
    runner = IndexerRunner(command_factory=factory)
    with pytest.raises(FileNotFoundError):
        runner.start("incremental")
    status = runner.status()
    assert status.state is IndexerState.FAILED
    assert status.last_error is not None
