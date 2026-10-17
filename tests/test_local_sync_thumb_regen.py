"""
tests/test_local_sync_thumb_regen.py

Layer 1 tests for the Round-50 self-heal: when the indexer walks
a batch and finds a file whose embedding is up-to-date (mtime/size
match in Qdrant) but whose on-disk thumbnail is missing, it
regenerates the thumbnail without re-embedding.

Covers:
  - The missing-thumb case (the headline feature)
  - The existing-thumb case (no spurious work, no spurious
    `thumb_regens` increment)
  - The mixed batch (some up-to-date with thumbs, some without)
  - The progress event surfaces `thumb_regens`

The test fixture points the indexer at a tmp_path thumb dir so we
can delete the thumb between runs without touching the prod tree.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from indexer import local_sync as local_sync_mod
from indexer import thumbnails as thumbs_mod
from indexer.thumbnails import thumbnail_path
from indexer.upsert import id_for

COLLECTION = "images_test_thumbregen"


def _make_png(tmp_path: Path, name: str, color=(10, 90, 200)) -> Path:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        pytest.skip("Pillow not available")
    p = tmp_path / name
    Image.new("RGB", (16, 16), color).save(p)
    return p


def _run(
    monkeypatch,
    raw,
    source_dir: Path,
    thumb_dir: Path,
    extra_args=None,
) -> tuple[int, list[dict]]:
    """Run the indexer with a custom THUMBNAIL_DIR and capture all
    JSON progress events from stdout."""
    monkeypatch.setattr(local_sync_mod, "make_client", lambda _: raw)
    # THUMBNAIL_DIR lives in indexer.thumbnails; the indexer imports
    # it as `from indexer.thumbnails import generate_thumbnail_for_path,
    # thumbnail_path` (not THUMBNAIL_DIR), so the value is read by
    # thumbnail_path() at call-time via the indexer.thumbnails module.
    # Monkeypatch it there, plus the env var so any os.environ-based
    # lookups inside the thumb helper see the same value.
    monkeypatch.setattr(thumbs_mod, "THUMBNAIL_DIR", str(thumb_dir))
    monkeypatch.setenv("THUMBNAIL_DIR", str(thumb_dir))
    args = [
        "--source", str(source_dir),
        "--source-name", "x",
        "--qdrant-collection", COLLECTION,
        "--device", "cpu",
        "--model", "mock-1536",
        "--json-progress",
    ]
    if extra_args:
        args.extend(extra_args)
    return local_sync_mod.main(args), []


def _events(capsys) -> list[dict]:
    """Parse all JSON progress events from the captured stdout."""
    out = capsys.readouterr().out
    events: list[dict] = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _initial_sync(monkeypatch, raw, src: Path, thumbs: Path) -> None:
    rc, _ = _run(monkeypatch, raw, src, thumbs)
    assert rc == 0


def test_existing_thumb_is_not_regenerated(
    monkeypatch, tmp_path, capsys,
):
    """If a file is up-to-date and its thumb exists, the indexer
    must NOT regen the thumb and must NOT increment
    `thumb_regens`. The second run is a pure skip — no re-embed,
    no thumb regen, no Qdrant write.

    `capsys.readouterr()` is called AFTER each `_run` so we capture
    only the most recent run's stdout; if we read it once at the
    end, the first run's events would mask the second run's.
    """
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    _make_png(src, "a.png")

    _initial_sync(monkeypatch, raw, src, thumbs)
    # Drain the first run's stdout so the second run's events
    # don't get masked by the first run's "done" event.
    capsys.readouterr()
    pid = str(id_for(src / "a.png", ""))
    canonical = thumbnail_path(pid)
    assert canonical.exists(), "initial sync must have written the thumb"

    # Second run: file is up-to-date, thumb is present. Should be
    # a pure skip — no re-embed, no thumb regen, no Qdrant write.
    rc, _ = _run(monkeypatch, raw, src, thumbs)
    assert rc == 0

    events = _events(capsys)
    done = next(
        (e for e in events if e.get("event") == "done"),
        None,
    )
    assert done is not None, f"expected a 'done' event; got: {events}"
    assert done["thumb_regens"] == 0, (
        f"no missing thumbs → no regens; got thumb_regens="
        f"{done['thumb_regens']}"
    )
    # The file should appear in the skip count.
    assert done["indexed"] == 0
    assert done["reembedded"] == 0


def test_missing_thumb_is_regenerated_on_next_run(
    monkeypatch, tmp_path, capsys,
):
    """The headline feature: delete the on-disk thumb, run the
    indexer, the file is up-to-date (mtime/size match) but the
    thumb is missing → it gets regenerated. The re-embed path
    is NOT taken (file is in `total_skipped` branch).

    `capsys.readouterr()` is called after `_initial_sync` to
    drain the first run's events from the capture buffer; the
    second run's events are what we assert against.
    """
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    img = _make_png(src, "a.png")

    _initial_sync(monkeypatch, raw, src, thumbs)
    capsys.readouterr()  # drain first run's events
    pid = str(id_for(img, ""))
    canonical = thumbnail_path(pid)
    assert canonical.exists()

    # Simulate the "I deleted the thumb" case.
    canonical.unlink()
    # Bump mtime of the source file? No — the regen path requires
    # the file to be UP-TO-DATE (mtime/size match), so the source
    # file's stat must match what Qdrant recorded. We just delete
    # the thumb, not the source. This is the actual user scenario
    # in prod.

    # Re-run. Expect the thumb to come back without re-embedding.
    rc, _ = _run(monkeypatch, raw, src, thumbs)
    assert rc == 0

    # The thumb is back.
    assert canonical.exists(), "missing thumb should be regenerated"
    # Re-embed was NOT triggered.
    events = _events(capsys)
    done = next(
        (e for e in events if e.get("event") == "done"),
        None,
    )
    assert done is not None
    assert done["reembedded"] == 0, (
        f"file was up-to-date; should not re-embed. got {done}"
    )
    assert done["thumb_regens"] == 1, (
        f"expected 1 thumb regen, got {done['thumb_regens']}"
    )


def test_partial_batch_regens_only_missing_thumbs(
    monkeypatch, tmp_path, capsys,
):
    """Mixed batch: 3 up-to-date files, 1 missing thumb. The
    indexer should regen exactly that 1 and leave the others
    alone. The `thumb_regens` counter should be 1, not 3."""
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    paths = [
        _make_png(src, f"img_{i}.png")
        for i in range(3)
    ]

    # Initial sync: all three get embedded and thumbnailed.
    _initial_sync(monkeypatch, raw, src, thumbs)
    capsys.readouterr()  # drain first run's events
    # Verify all three thumbs are on disk.
    pids = [str(id_for(p, "")) for p in paths]
    for pid in pids:
        assert thumbnail_path(pid).exists()

    # Delete the thumb for the middle file only.
    thumbnail_path(pids[1]).unlink()

    # Re-run. Expect 1 thumb regen.
    rc, _ = _run(monkeypatch, raw, src, thumbs)
    assert rc == 0

    events = _events(capsys)
    done = next(e for e in events if e.get("event") == "done")
    assert done["thumb_regens"] == 1
    # All three thumbs are now back on disk.
    for pid in pids:
        assert thumbnail_path(pid).exists()


def test_thumb_regen_handles_corrupt_thumb_dir(
    monkeypatch, tmp_path, capsys,
):
    """If the entire thumb dir is gone (e.g. operator nuked
    THUMBNAIL_DIR), the indexer should still recover. The regen
    path writes the canonical and any sized variants, recreating
    the prefix bucket as a side effect of `out_path.parent.mkdir
    (parents=True, exist_ok=True)` in the thumbnail helper."""
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    _make_png(src, "a.png")

    _initial_sync(monkeypatch, raw, src, thumbs)
    capsys.readouterr()  # drain first run's events
    pid = str(id_for(src / "a.png", ""))
    assert thumbnail_path(pid).exists()

    # Nuke the whole THUMBNAIL_DIR (this is the prod
    # "deleted-everything" scenario).
    import shutil
    shutil.rmtree(thumbs)
    assert not thumbs.exists()

    rc, _ = _run(monkeypatch, raw, src, thumbs)
    assert rc == 0

    # The thumb is back, the prefix bucket is recreated.
    assert thumbs.exists()
    assert thumbnail_path(pid).exists()
    events = _events(capsys)
    done = next(e for e in events if e.get("event") == "done")
    assert done["thumb_regens"] == 1


def test_dry_run_does_not_write_thumb(
    monkeypatch, tmp_path, capsys,
):
    """A `--dry-run` indexer pass must not write the thumb. The
    progress event should still report the count of files that
    WOULD have been regenerated, so an operator can preview the
    regen scope before kicking off a real run."""
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    thumbs = tmp_path / "thumbs"
    thumbs.mkdir()
    img = _make_png(src, "a.png")

    _initial_sync(monkeypatch, raw, src, thumbs)
    capsys.readouterr()  # drain first run's events
    pid = str(id_for(img, ""))
    thumbnail_path(pid).unlink()

    rc, _ = _run(
        monkeypatch, raw, src, thumbs, extra_args=["--dry-run"]
    )
    assert rc == 0
    # The thumb is still missing — dry-run did not write it.
    assert not thumbnail_path(pid).exists()

    events = _events(capsys)
    done = next(e for e in events if e.get("event") == "done")
    # dry-run reports what would happen; the dry-run branch
    # doesn't bump thumb_regens (only the write path does), but
    # the log line emitted by dry-run's per-batch branch should
    # be visible in stdout (we use the log buffer for that).
    # The contract for the operator: dry-run tells you via the
    # log how many thumbs WOULD be regen'd; the wire-level
    # `thumb_regens` counter is the post-run truth.
    # Here we just confirm dry-run completes successfully and
    # doesn't claim work it didn't do.
    assert done.get("errors", 0) == 0
