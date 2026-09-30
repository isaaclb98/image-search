"""
test_indexer_writes_search_cache.py — Round-35: indexer mirrors
Qdrant upserts into the search-side `images.db` so Likes /
Dislikes / Random see fresh points during a long indexing run
without waiting for the periodic /api/cache/refresh.

The shape of this test is a Qdrant in-memory + a real on-disk
SQLite at `tmp_path / "images.db"` (matching how the in-app
indexer runs against the search container's `/app/data/images.db`).
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from indexer import upsert
from indexer.upsert import VECTOR_DIM


def _seed_payload(path: str = "/photos/a.jpg", collection: str = "kpop") -> dict:
    """Build a Qdrant payload shaped like build_payload()'s output."""
    return {
        "id": str(uuid.uuid4()),
        "path": path,
        "shard": "",
        "collection": collection,
        "blurhash": "L:abc:",
        "folder": "/photos",
        "width": 1024,
        "height": 768,
        "mtime": 1700000000,
        "size": 12345,
        "model_name": "ViT-B-16-SigLIP2-256",
        "model_revision": "",
        "model_dim": 768,
        "indexed_at": "2026-01-01T00:00:00Z",
    }


def _open_sqlite_with_schema(path):
    """Open a fresh SQLite at `path` and create the images table.

    Mirrors what the in-app search container does at startup so we
    exercise the same schema the indexer writes against in prod.
    """
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS images (
          id            TEXT PRIMARY KEY,
          path          TEXT NOT NULL,
          shard         TEXT DEFAULT '',
          collection    TEXT DEFAULT '',
          mtime         INTEGER,
          size          INTEGER,
          indexed_at    TEXT,
          width         INTEGER,
          height         INTEGER,
          blurhash       TEXT DEFAULT ''
        );
        """
    )
    conn.commit()
    return conn


def test_upsert_batch_writes_to_sqlite(tmp_path):
    """Each successful upsert also writes a row to images.db."""
    from qdrant_client import QdrantClient

    client = QdrantClient(location=":memory:")
    coll = "images_test_mirror"
    upsert.ensure_collection(client, coll, dim=VECTOR_DIM)

    sqlite_path = tmp_path / "images.db"
    sqlite_conn = _open_sqlite_with_schema(sqlite_path)

    payload = _seed_payload()
    items = [(payload["id"], [0.0] * VECTOR_DIM, payload)]
    upsert.upsert_batch(client, coll, items, wait=True, sqlite_conn=sqlite_conn)

    # Verify the row landed in SQLite with all expected columns.
    rows = sqlite_conn.execute(
        "SELECT id, path, shard, collection, mtime, size, indexed_at, width, height, blurhash "
        "FROM images"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == payload["id"]
    assert row[1] == payload["path"]
    assert row[2] == payload["shard"]
    assert row[3] == payload["collection"]
    assert row[4] == payload["mtime"]
    assert row[5] == payload["size"]
    assert row[6] == payload["indexed_at"]
    assert row[7] == payload["width"]
    assert row[8] == payload["height"]
    assert row[9] == payload["blurhash"]


def test_upsert_batch_skips_sqlite_when_conn_is_none(tmp_path):
    """Passing sqlite_conn=None means Qdrant-only mode (test fixture path).

    Asserts no exception — the Qdrant upsert should still succeed
    even when there's nowhere to mirror to.
    """
    from qdrant_client import QdrantClient

    client = QdrantClient(location=":memory:")
    coll = "images_test_no_mirror"
    upsert.ensure_collection(client, coll, dim=VECTOR_DIM)

    payload = _seed_payload(path="/photos/no_mirror.jpg")
    items = [(payload["id"], [0.0] * VECTOR_DIM, payload)]
    # Must not raise.
    upsert.upsert_batch(client, coll, items, wait=True, sqlite_conn=None)

    # Verify Qdrant got the point.
    pts = client.retrieve(coll, ids=[payload["id"]])
    assert len(pts) == 1


def test_upsert_batch_handles_missing_optional_payload_fields(tmp_path):
    """Older points (or non-standard callers) may not populate width/height.

    The mirror must still write a row with width/height = NULL.
    """
    from qdrant_client import QdrantClient

    client = QdrantClient(location=":memory:")
    coll = "images_test_partial_payload"
    upsert.ensure_collection(client, coll, dim=VECTOR_DIM)

    sqlite_path = tmp_path / "images.db"
    sqlite_conn = _open_sqlite_with_schema(sqlite_path)

    # Drop width/height to mimic legacy payload shape.
    payload = _seed_payload()
    payload.pop("width")
    payload.pop("height")
    items = [(payload["id"], [0.0] * VECTOR_DIM, payload)]
    upsert.upsert_batch(client, coll, items, wait=True, sqlite_conn=sqlite_conn)

    row = sqlite_conn.execute(
        "SELECT width, height FROM images WHERE id = ?", (payload["id"],)
    ).fetchone()
    assert row is not None
    assert row[0] is None
    assert row[1] is None


def test_upsert_batch_is_idempotent(tmp_path):
    """Re-upserting the same id updates the row (INSERT OR REPLACE)."""
    from qdrant_client import QdrantClient

    client = QdrantClient(location=":memory:")
    coll = "images_test_idempotent"
    upsert.ensure_collection(client, coll, dim=VECTOR_DIM)

    sqlite_path = tmp_path / "images.db"
    sqlite_conn = _open_sqlite_with_schema(sqlite_path)

    payload = _seed_payload(path="/photos/v1.jpg")
    items = [(payload["id"], [0.0] * VECTOR_DIM, payload)]
    upsert.upsert_batch(client, coll, items, wait=True, sqlite_conn=sqlite_conn)

    # Re-upsert with the same id but a different path (e.g. user
    # moved the file).
    payload["path"] = "/photos/v2.jpg"
    upsert.upsert_batch(client, coll, items, wait=True, sqlite_conn=sqlite_conn)

    rows = sqlite_conn.execute(
        "SELECT path FROM images WHERE id = ?", (payload["id"],)
    ).fetchall()
    assert len(rows) == 1, "INSERT OR REPLACE should keep one row, not append"
    assert rows[0][0] == "/photos/v2.jpg"


def test_search_index_db_uses_wal_mode(tmp_path):
    """The search-side IndexDB connection enables WAL mode so the
    indexer subprocess can write concurrently without blocking the
    search app's reads.
    """
    from unittest.mock import MagicMock

    from search.index_db import IndexDB

    db_path = tmp_path / "images.db"
    # IndexDB requires a QdrantSearch for init_from_qdrant. The
    # constructor doesn't actually use it for the WAL test — we only
    # care that the PRAGMA is set on the freshly-opened connection.
    fake_qdrant = MagicMock()
    index_db = IndexDB(db_path=str(db_path), qdrant_client=fake_qdrant)

    # Read the journal_mode pragma directly from the file.
    conn = sqlite3.connect(str(db_path))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"