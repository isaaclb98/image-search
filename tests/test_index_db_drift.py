"""Tests for the drift-aware IndexDB refresh (marker/count driven)."""
from __future__ import annotations

import pytest

from indexer.sync_meta import ensure_sync_collections, write_meta
from search.index_db import IndexDB
from search.qdrant_client import QdrantSearch
from search.text_encoder import _mock_embed

COLLECTION = "images_test_drift"


def _pid(name: str) -> str:
    """Deterministic UUID point id (in-memory Qdrant requires UUIDs)."""
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"drift-{name}"))


@pytest.fixture
def drift_env(tmp_path):
    from qdrant_client import QdrantClient

    raw = QdrantClient(location=":memory:")
    ensure_sync_collections(raw, COLLECTION)
    qdrant = QdrantSearch(client=raw, collection=COLLECTION, timeout_ms=2000)
    db = IndexDB(str(tmp_path / "images.db"), qdrant, refresh_interval_seconds=3600)
    try:
        yield {"raw": raw, "qdrant": qdrant, "db": db}
    finally:
        db.close()


def _seed(raw, pid: str, path: str, mtime: int = 100, size: int = 200) -> None:
    from qdrant_client.http import models as qmodels
    raw.upsert(
        collection_name=COLLECTION,
        points=[qmodels.PointStruct(
            id=_pid(pid),
            vector=_mock_embed(pid),
            payload={
                "id": _pid(pid), "path": path, "shard": "", "collection": "photos",
                "mtime": mtime, "size": size, "indexed_at": "2026-01-01T00:00:00+00:00",
            },
        )],
        wait=True,
    )


def _marker(raw, run_id: str, changed: bool, **extra) -> None:
    write_meta(raw, {"scanner_run_id": run_id, "scanner_changed": changed, **extra})


def _rows(db) -> dict:
    return {r["id"]: r for r in db.pick_random_rows(10000)}


def test_merge_from_qdrant_is_incremental(drift_env):
    raw, db = drift_env["raw"], drift_env["db"]
    _seed(raw, "a", "/photos/a.jpg")
    _seed(raw, "b", "/photos/b.jpg")
    assert db.init_from_qdrant() == 2

    # Update b's signature, add c, drop a.
    _seed(raw, "b", "/photos/b.jpg", mtime=999, size=999)
    _seed(raw, "c", "/photos/c.jpg")
    raw.delete(collection_name=COLLECTION, points_selector=[_pid("a")], wait=True)

    counts = db.merge_from_qdrant()
    assert counts == {"added": 1, "updated": 1, "removed": 1}
    rows = _rows(db)
    assert set(rows) == {_pid("b"), _pid("c")}
    assert rows[_pid("b")]["mtime"] == 999


def test_refresh_if_changed_no_drift_is_noop(drift_env):
    raw, db = drift_env["raw"], drift_env["db"]
    _seed(raw, "a", "/photos/a.jpg")
    db.init_from_qdrant()
    _marker(raw, "run-1", changed=False)
    db.refresh_if_changed()  # store baseline marker

    result = db.refresh_if_changed()
    assert result == {"refreshed": False}


def test_refresh_if_changed_merges_on_marker_change(drift_env):
    raw, db = drift_env["raw"], drift_env["db"]
    _seed(raw, "a", "/photos/a.jpg")
    db.init_from_qdrant()
    _marker(raw, "run-1", changed=False)
    db.refresh_if_changed()

    # Scanner run 2: one new file queued+embedded, one orphan proposed.
    _seed(raw, "b", "/photos/b.jpg")
    _marker(raw, "run-2", changed=True, scanner_new=1, scanner_orphans=1)
    result = db.refresh_if_changed()
    assert result["refreshed"] is True
    assert result["counts"]["added"] == 1
    assert set(_rows(db)) == {_pid("a"), _pid("b")}

    # Marker with no changes -> no-op again.
    _marker(raw, "run-3", changed=False)
    result = db.refresh_if_changed()
    assert result["refreshed"] is False


def test_refresh_if_changed_detects_out_of_band_delete(drift_env):
    raw, db = drift_env["raw"], drift_env["db"]
    _seed(raw, "a", "/photos/a.jpg")
    _seed(raw, "b", "/photos/b.jpg")
    db.init_from_qdrant()
    _marker(raw, "run-1", changed=False)
    db.refresh_if_changed()

    # Someone deletes a point outside the scanner flow (admin, old tool).
    raw.delete(collection_name=COLLECTION, points_selector=[_pid("a")], wait=True)
    result = db.refresh_if_changed()
    assert result["refreshed"] is True
    assert result["counts"]["removed"] == 1
    assert set(_rows(db)) == {_pid("b")}


def test_refresh_if_changed_falls_back_without_marker(drift_env):
    raw, db = drift_env["raw"], drift_env["db"]
    _seed(raw, "a", "/photos/a.jpg")
    db.init_from_qdrant()
    result = db.refresh_if_changed()  # no _sync_meta marker at all
    assert result["refreshed"] is True


def test_merge_preserves_favorites(drift_env):
    raw, db = drift_env["raw"], drift_env["db"]
    _seed(raw, "a", "/photos/a.jpg")
    db.init_from_qdrant()
    db.mark_favorite(_pid("a"))
    _marker(raw, "run-1", changed=False)
    db.refresh_if_changed()

    _marker(raw, "run-2", changed=True)
    db.refresh_if_changed()
    assert _pid("a") in db.list_favorite_ids()


def test_pending_count_and_marker_status(drift_env):
    raw, db = drift_env["raw"], drift_env["db"]
    assert db.pending_count() == 0
    _marker(raw, "run-9", changed=True)
    db.refresh_if_changed()
    assert "run-9" in (db.last_scanner_run_id() or "")
