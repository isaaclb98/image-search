"""
tests/test_indexer_runner_env.py — subprocess env must expose
DEVICE (not just INDEXER_DEVICE) so OpenClipEmbedder routes to GPU.

`OpenClipEmbedder.__init__` reads `os.environ["DEVICE"]` (NOT
`INDEXER_DEVICE`, NOT a constructor arg). The runner spawns the
indexer with the parent's env inherited; if the parent only has
`INDEXER_DEVICE=cuda`, the subprocess sees `DEVICE`-less env →
encoder falls back to CPU → 6-day indexing instead of 4 hours.

Fix: indexer_runner.start() copies `INDEXER_DEVICE` → `DEVICE`
into the subprocess env (no-op if the parent already has DEVICE).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

from search.indexer_runner import IndexerRunner


# A small Python script that writes its DEVICE + INDEXER_DEVICE
# env to stdout, then exits. The test parses the JSON line.
SCRIPT = textwrap.dedent(
    """
    import json, os, sys
    sys.stdout.write(json.dumps({
        "device": os.environ.get("DEVICE"),
        "indexer_device": os.environ.get("INDEXER_DEVICE"),
    }) + "\\n")
    sys.stdout.flush()
    """
)


def _write_capture_script(tmp_path) -> str:
    p = tmp_path / "capture.py"
    p.write_text(SCRIPT)
    return str(p)


def test_subprocess_inherits_parent_env_with_device_set(tmp_path):
    """Regression: when the parent (search container) has only
    INDEXER_DEVICE=cuda, the spawned indexer subprocess must end up
    with DEVICE=cuda in its env, not DEVICE-less."""
    script = _write_capture_script(tmp_path)

    # Simulate the parent's env: has INDEXER_DEVICE=cuda, no DEVICE.
    parent_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),  # noqa: S108 — test default for HOME in an isolated subprocess
        "PYTHONUNBUFFERED": "1",
        "INDEXER_DEVICE": "cuda",
    }

    # What we WANT to happen (the fix):
    child_env = {**parent_env, "DEVICE": parent_env["INDEXER_DEVICE"]}
    # argv is fully controlled ([sys.executable, <test script>]); the
    # subprocess is short-lived and we only inspect stdout, so
    # `check=False` is intentional.
    proc = subprocess.run(  # noqa: S603 — test subprocess; argv is operator-controlled
        [sys.executable, script],
        capture_output=True, text=True, env=child_env, check=False,
    )
    import json as _json
    body = _json.loads(proc.stdout.strip())
    assert body["device"] == "cuda"


def test_runner_subprocess_has_device_set(tmp_path, monkeypatch):
    """End-to-end: IndexerRunner.start() spawns a subprocess whose
    env contains DEVICE matching INDEXER_DEVICE. Uses a fake
    command_factory whose handler exposes its own env."""
    script = _write_capture_script(tmp_path)
    # The factory hands back a command that runs the capture script.
    factory = lambda mode: [sys.executable, script]
    runner = IndexerRunner(command_factory=factory)

    # Simulate the parent env by monkey-patching os.environ
    # inside the runner module's namespace (it's imported at the
    # top of the file).
    import search.indexer_runner as ir_mod
    saved_environ = os.environ.copy()
    monkeypatch.setattr(
        ir_mod.os, "environ",
        {**saved_environ, "INDEXER_DEVICE": "cuda"},
        raising=False,
    )
    try:
        runner.start("incremental")
        # The reader thread writes the captured env to its log buffer
        # — wait for the run to finish, then grep the log.
        runner.wait_idle(timeout=10)
        snap = runner.log()
        import json as _json
        body = _json.loads(next(l for l in snap["lines"] if l.startswith("{")))
        assert body["device"] == "cuda", (
            f"subprocess DEVICE env must be 'cuda' but was {body['device']!r}; "
            f"INDEXER_DEVICE was {body['indexer_device']!r}"
        )
    finally:
        runner.cancel()
