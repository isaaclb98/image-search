"""
tests/test_local_sync_change_detection.py

Layer 1 tests for local_sync change detection (mtime/size-based re-embed).

Guards:
- unchanged files are skipped (no re-embed, no second point)
- files whose mtime/size changed are re-embedded INTO the same
  deterministic point id (favourites/album membership survives)
- legacy points without stored mtime/size are treated as changed,
  so they heal on the next sync
- dry-run reports what would be embedded without touching the collection
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from indexer import local_sync as local_sync_mod
from indexer.upsert import id_for

COLLECTION = "images_test_changedetect"


def _make_png(tmp_path: Path, name: str, color=(10, 90, 200)) -> Path:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        pytest.skip("Pillow not available")
    p = tmp_path / name
    Image.new("RGB", (16, 16), color).save(p)
    return p


def _run(monkeypatch, raw, source_dir: Path, extra_args=None) -> int:
    monkeypatch.setattr(local_sync_mod, "make_client", lambda _: raw)
    args = [
        "--source", str(source_dir), "--source-name", "x",
        "--qdrant-collection", COLLECTION,
        "--device", "cpu",
    ]
    if extra_args:
        args.extend(extra_args)
    return local_sync_mod.main(args)


def _points(raw) -> list:
    if not raw.collection_exists(COLLECTION):
        return []
    pts, _ = raw.scroll(
        collection_name=COLLECTION, limit=50,
        with_payload=True, with_vectors=True,
    )
    return pts


def _initial_sync(monkeypatch, raw, src: Path) -> None:
    rc = _run(monkeypatch, raw, src)
    assert rc == 0


def test_unchanged_file_not_reembedded(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    _make_png(src, "a.png")

    _initial_sync(monkeypatch, raw, src)
    pts = _points(raw)
    assert len(pts) == 1
    first_id = str(pts[0].id)
    first_mtime = pts[0].payload["mtime"]

    rc = _run(monkeypatch, raw, src)
    assert rc == 0

    pts = _points(raw)
    assert len(pts) == 1, "unchanged file must not produce a second point"
    assert str(pts[0].id) == first_id
    assert pts[0].payload["mtime"] == first_mtime


def test_changed_file_reembedded_into_same_point(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    img = _make_png(src, "a.png")

    _initial_sync(monkeypatch, raw, src)
    pts = _points(raw)
    assert len(pts) == 1
    old_id = str(pts[0].id)
    old_vec = list(pts[0].vector)

    # Modify file: new size + mtime. Bump mtime explicitly so the
    # change is visible regardless of filesystem timestamp granularity.
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        pytest.skip("Pillow not available")
    Image.new("RGB", (32, 32), (255, 0, 0)).save(img)
    now = time.time()
    os.utime(img, (now + 5, now + 5))

    rc = _run(monkeypatch, raw, src)
    assert rc == 0

    pts = _points(raw)
    assert len(pts) == 1, "changed file must re-embed IN PLACE, not duplicate"
    assert str(pts[0].id) == old_id, "point id must stay stable across re-embed"
    assert list(pts[0].vector) != old_vec, "re-embed must update the vector"


def test_legacy_point_without_mtime_gets_reembedded(monkeypatch, tmp_path):
    """Points indexed before change detection lack mtime/size in the
    payload. They must be treated as changed so they heal on next run."""
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    img = _make_png(src, "a.png")

    _initial_sync(monkeypatch, raw, src)
    pid = str(id_for(img, ""))
    # Strip mtime/size to simulate legacy index state.
    raw.overwrite_payload(
        collection_name=COLLECTION,
        points=[pid],
        payload={"mtime": None, "size": None},
    )

    rc = _run(monkeypatch, raw, src)
    assert rc == 0
    pts = _points(raw)
    assert len(pts) == 1
    assert pts[0].payload.get("mtime") is not None, "legacy point must heal"


def test_dry_run_reports_would_embed_without_writing(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    _make_png(src, "a.png")
    _make_png(src, "b.png")

    rc = _run(monkeypatch, raw, src, extra_args=["--dry-run"])
    assert rc == 0
    assert _points(raw) == [], "dry-run must not write points"


def test_prune_dry_run_still_no_writes(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    _make_png(src, "a.png")
    _initial_sync(monkeypatch, raw, src)
    (src / "a.png").unlink()

    rc = _run(monkeypatch, raw, src, extra_args=["--prune", "--dry-run"])
    assert rc == 0
    pts = _points(raw)
    assert len(pts) == 1, "dry-run prune must not delete points"


def test_full_flag_mutually_exclusive_with_backfill_flags(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    monkeypatch.setattr(local_sync_mod, "make_client", lambda _: raw)
    rc = local_sync_mod.main([
        "--source", str(src), "--source-name", "x",
        "--qdrant-collection", COLLECTION,
        "--full", "--reblurhash",
    ])
    assert rc == 2


def test_full_sweep_embeds_new_and_heals_legacy(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    img = _make_png(src, "a.png")

    _initial_sync(monkeypatch, raw, src)

    # Simulate a legacy point missing blurhash + fingerprint.
    pid = str(id_for(img, ""))
    pts = _points(raw)
    legacy = dict(pts[0].payload)
    legacy["blurhash"] = None
    legacy.pop("content_sha256", None)
    legacy.pop("dhash", None)
    raw.overwrite_payload(
        collection_name=COLLECTION, points=[pid], payload=legacy,
    )

    rc = _run(monkeypatch, raw, src, extra_args=["--full"])
    assert rc == 0

    pts = _points(raw)
    assert len(pts) == 1
    assert pts[0].payload.get("blurhash") is not None, "blurhash healed"
    assert pts[0].payload.get("content_sha256") is not None, "sha healed"
    assert pts[0].payload.get("dhash") is not None, "dhash healed"


def test_full_dry_run_writes_nothing(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    _make_png(src, "a.png")
    rc = _run(monkeypatch, raw, src, extra_args=["--full", "--dry-run"])
    assert rc == 0
    assert _points(raw) == [], "dry-run --full must not write"
