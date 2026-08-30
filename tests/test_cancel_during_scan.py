"""
tests/test_cancel_during_scan.py — indexer must respect SIGTERM
during the filesystem snapshot walk.

Bug: round 31 shipped cooperative cancel via SIGTERM but only
checked the cancel flag at batch boundaries. For libraries with
hundreds of thousands of files, `scan.snapshot()` can take minutes
to complete — cancel during that window was effectively ignored.

Fix: `snapshot()` accepts a `should_cancel` callback polled every
`cancel_check_every` files. Raises `ScanCancelled` if the callback
returns True. local_sync translates this to exit code 130 and a
"cancelled" progress event.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from indexer import scan as scan_mod


def _make_files(root: Path, n: int) -> None:
    for i in range(n):
        (root / f"img_{i:05d}.jpg").write_bytes(b"x")


def test_snapshot_completes_without_cancel(tmp_path: Path):
    _make_files(tmp_path, 50)
    result = scan_mod.snapshot(tmp_path, should_cancel=lambda: False)
    assert len(result) == 50


def test_snapshot_raises_scan_cancelled_when_flag_set(tmp_path: Path):
    _make_files(tmp_path, 200)

    # Cancel after 30 files found — fire on the 4th cancel-check
    # call (every `cancel_check_every=5` files, so checks fire at
    # counts 5, 10, 15, 20, 25, 30 → fire at the 6th check).
    counter = {"n": 0}

    def should_cancel() -> bool:
        counter["n"] += 1
        return counter["n"] >= 6

    with pytest.raises(scan_mod.ScanCancelled):
        scan_mod.snapshot(
            tmp_path,
            should_cancel=should_cancel,
            cancel_check_every=5,
            progress_every=100,
        )
    assert counter["n"] >= 6


def test_local_sync_handles_scan_cancelled(tmp_path: Path, monkeypatch):
    """When snapshot() raises ScanCancelled, local_sync exits 130
    and emits a 'cancelled' progress event (same as batch-boundary
    cancel)."""
    from indexer import local_sync as ls

    _make_files(tmp_path, 200)

    # Force ScanCancelled by passing a should_cancel that fires
    # immediately. We do this by monkeypatching snapshot.
    def boom(*args, **kwargs):
        raise scan_mod.ScanCancelled()

    monkeypatch.setattr(ls.scan_mod, "snapshot", boom)
    monkeypatch.setattr(scan_mod, "snapshot", boom)

    out = []
    monkeypatch.setattr("sys.stdout.write", lambda s: out.append(s))

    # Bypass _install_signal_handlers' main-thread requirement.
    ls._cancel_event = threading.Event()

    rc = ls.main([
        "--source", str(tmp_path),
        "--json-progress",
        "--qdrant-in-memory",
        "--dry-run",
    ])

    assert rc == 130
    progress_lines = [l for l in out if l.startswith("{")]
    # json.dumps uses separators=(",", ":") so no whitespace — search
    # for the space-less form.
    assert any('"event":"cancelled"' in l for l in progress_lines), (
        f"expected 'cancelled' event, got: {progress_lines}"
    )


def test_snapshot_backwards_compatible_without_callback(tmp_path: Path):
    """snapshot() called without should_cancel keeps the old
    behavior (full walk, no cancellation). Existing callers in
    tests/scripts don't need to change."""
    _make_files(tmp_path, 20)
    result = scan_mod.snapshot(tmp_path)
    assert len(result) == 20
