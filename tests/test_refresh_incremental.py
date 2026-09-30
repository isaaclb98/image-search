"""
test_refresh_incremental.py — Round-35: focused tests for
IndexDB.refresh_incremental(). Covers the three states:

  1. In sync (count fast path) — no Qdrant scroll, no writes
  2. New ids only (drift = +K) — INSERT only
  3. Orphan ids only (drift = -K) — DELETE only
  4. Mixed drift — both INSERT and DELETE
  5. Orphan-with-no-qdrant-correspondence (deleted)
  6. Favorites for orphan ids are purged via the same
     purge_orphaned_user_data path the wipe+rebuild used.

The fixture seeds an in-memory Qdrant and a SQLite cache via
the same `refresh_app` fixture that test_cache_refresh.py uses
so the full path is exercised end-to-end.
"""

from __future__ import annotations

import pytest

from indexer import upsert
from indexer.upsert import VECTOR_DIM


def _seed_qdrant(client, collection: str, count: int) -> list[str]:
    """Upsert `count` points with deterministic ids + payloads."""
    items = []
    for i in range(count):
        pid = _qdrant_id_for(i)
        items.append((
            pid,
            [0.0] * VECTOR_DIM,
            {
                "id": pid,
                "path": f"/photos/{i}.jpg",
                "collection": "kpop",
                "mtime": 1700000000,
                "size": 1024,
                "indexed_at": "2026-01-01T00:00:00Z",
                "blurhash": "L:abc:",
            },
        ))
    upsert.upsert_batch(client, collection, items, wait=True)
    return [item[0] for item in items]


def _qdrant_id_for(i: int) -> str:
    return f"00000000-0000-0000-0000-{i:012d}"


@pytest.fixture
def seeded_index_db(refresh_app):
    """Yields (index_db, qdrant_client, collection) wired via the
    refresh_app fixture. Tests can call upsert_batch() on the
    client and refresh_incremental() on the index_db.
    """
    index_db = refresh_app.app.state.index_db
    client = refresh_app.app.state.qdrant_client
    collection = refresh_app.app.state.qdrant_collection
    return index_db, client, collection


@pytest.fixture
def refresh_app(tmp_path, monkeypatch):
    """A FastAPI app wired to an in-memory Qdrant + SQLite. Mirrors
    the shape of test_cache_refresh.py's `refresh_app` fixture so
    the refresh path is exercised end-to-end without going through
    the network.
    """
    from pathlib import Path as _Path

    from fastapi.testclient import TestClient

    from indexer import upsert
    from qdrant_client import QdrantClient
    from search import app as _app_mod
    from search.app import create_app
    from search.config import Config
    from search.qdrant_client import QdrantSearch
    monkeypatch.setattr(
        _app_mod,
        "resolve_local",
        lambda path, *a, **kw: _Path(path) if path else None,
    )
    monkeypatch.setattr(_app_mod, "_is_path_alive", lambda path: True)

    cfg = Config(
        qdrant_url="memory://",
        qdrant_collection="images_test_incremental",
        qdrant_api_key=None,
        model_name="mock",
        model_revision="",
        device="cpu",
        top_k_default=50,
        top_k_max=200,
        query_timeout_ms=2000,
        nas_images_base=str(tmp_path),
        path_prefix="",
        web_ui_url="http://localhost:8000",
        log_level="WARNING",
        index_db_path=str(tmp_path / "images.db"),
        test_mode=True,
    )
    client = QdrantClient(location=":memory:")
    upsert.ensure_collection(client, cfg.qdrant_collection, dim=VECTOR_DIM)
    qdrant = QdrantSearch(
        client=client, collection=cfg.qdrant_collection, timeout_ms=2000,
    )

    app = create_app(cfg=cfg, qdrant=qdrant)
    app.state.qdrant_client = client
    app.state.qdrant_collection = cfg.qdrant_collection
    return TestClient(app)


def test_refresh_incremental_fast_path_when_in_sync(seeded_index_db, refresh_app):
    """In-sync cache: refresh_incremental returns the skipped
    marker without writing anything.
    """
    index_db, client, collection = seeded_index_db

    # Seed 5 points; mirror them into SQLite via the API path
    # (which exercises the real upsert_records through the
    # refresh_incremental call's INSERT step).
    _seed_qdrant(client, collection, 5)
    refresh_app.post("/api/cache/refresh")

    stats = index_db.refresh_incremental()
    assert stats["skipped"] is True
    assert stats["qdrant_count"] == 5
    assert stats["sqlite_count"] == 5
    assert stats["inserted"] == 0
    assert stats["deleted"] == 0


def test_refresh_incremental_inserts_new_ids(seeded_index_db, refresh_app):
    """Drift = +N: refresh inserts only the new ids."""
    index_db, client, collection = seeded_index_db

    _seed_qdrant(client, collection, 3)
    refresh_app.post("/api/cache/refresh")

    # Add 2 more points directly via the indexer's upsert path
    # (bypassing the search-side cache to simulate an admin
    # insert or an indexer restart that wrote to Qdrant only).
    new_ids = [_qdrant_id_for(3), _qdrant_id_for(4)]
    upsert.upsert_batch(
        client, collection,
        [
            (
                pid,
                [0.0] * VECTOR_DIM,
                {
                    "id": pid,
                    "path": f"/photos/{i}.jpg",
                    "collection": "kpop",
                    "mtime": 1700000000,
                    "size": 1024,
                    "indexed_at": "2026-01-02T00:00:00Z",
                    "blurhash": "",
                },
            )
            for i, pid in zip([3, 4], new_ids)
        ],
        wait=True,
    )

    stats = index_db.refresh_incremental()
    assert stats["skipped"] is False
    assert stats["qdrant_count"] == 5
    assert stats["sqlite_count"] == 3
    assert stats["inserted"] == 2
    assert stats["deleted"] == 0

    # The new rows are in SQLite with their payload.
    for new_id in new_ids:
        row = index_db.get_by_id(new_id)
        assert row is not None
        assert row["path"] == f"/photos/{new_ids.index(new_id) + 3}.jpg"


def test_refresh_incremental_deletes_orphan_ids(seeded_index_db, refresh_app):
    """Drift = -N: refresh deletes only the orphan ids."""
    index_db, client, collection = seeded_index_db

    _seed_qdrant(client, collection, 5)
    refresh_app.post("/api/cache/refresh")

    # Externally delete 2 points from Qdrant.
    client.delete(
        collection_name=collection,
        points_selector=[_qdrant_id_for(0), _qdrant_id_for(1)],
        wait=True,
    )

    stats = index_db.refresh_incremental()
    assert stats["skipped"] is False
    assert stats["qdrant_count"] == 3
    assert stats["sqlite_count"] == 5
    assert stats["inserted"] == 0
    assert stats["deleted"] == 2

    # Orphans are gone from SQLite.
    assert index_db.get_by_id(_qdrant_id_for(0)) is None
    assert index_db.get_by_id(_qdrant_id_for(1)) is None
    # Survivors are still there.
    assert index_db.get_by_id(_qdrant_id_for(2)) is not None


def test_refresh_incremental_handles_mixed_drift(seeded_index_db, refresh_app):
    """Drift = +A / -B in the same tick: both INSERT and DELETE."""
    index_db, client, collection = seeded_index_db

    _seed_qdrant(client, collection, 5)
    refresh_app.post("/api/cache/refresh")

    # Delete 1, add 2.
    client.delete(
        collection_name=collection,
        points_selector=[_qdrant_id_for(0)],
        wait=True,
    )
    new_ids = [_qdrant_id_for(5), _qdrant_id_for(6)]
    upsert.upsert_batch(
        client, collection,
        [
            (
                pid,
                [0.0] * VECTOR_DIM,
                {
                    "id": pid,
                    "path": f"/photos/{i}.jpg",
                    "collection": "kpop",
                    "mtime": 1700000000,
                    "size": 1024,
                    "indexed_at": "2026-01-03T00:00:00Z",
                    "blurhash": "",
                },
            )
            for i, pid in zip([5, 6], new_ids)
        ],
        wait=True,
    )

    stats = index_db.refresh_incremental()
    assert stats["inserted"] == 2
    assert stats["deleted"] == 1

    assert index_db.get_by_id(_qdrant_id_for(0)) is None  # deleted
    assert index_db.get_by_id(_qdrant_id_for(5)) is not None  # inserted
    assert index_db.get_by_id(_qdrant_id_for(6)) is not None  # inserted


def test_refresh_incremental_preserves_favorites_for_surviving_rows(seeded_index_db, refresh_app):
    """Favorites for rows that survive the refresh are untouched.
    Favorites for orphan ids are purged (via the same
    purge_orphaned_user_data path the wipe+rebuild used).
    """
    index_db, client, collection = seeded_index_db

    _seed_qdrant(client, collection, 3)
    refresh_app.post("/api/cache/refresh")

    # Favourite the first id.
    fav_id = _qdrant_id_for(0)
    index_db.mark_favorite(fav_id)
    assert index_db.count_favorites() == 1

    # Delete that point from Qdrant.
    client.delete(collection_name=collection, points_selector=[fav_id], wait=True)

    # Refresh should detect the orphan and clean up the favorite.
    index_db.refresh_incremental()

    assert index_db.count_favorites() == 0  # favorite was purged

    # Other rows still in sync.
    assert index_db.get_by_id(_qdrant_id_for(1)) is not None
    assert index_db.get_by_id(_qdrant_id_for(2)) is not None