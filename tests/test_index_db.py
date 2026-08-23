from __future__ import annotations

import time

import pytest

from search.index_db import ImageNotInCacheError, IndexDB


class FakeQdrant:
    def __init__(self, batches):
        self.batches = batches
        self.refreshes = 0

    def scroll_all(self, batch_size=1000):
        self.refreshes += 1
        yield from self.batches


def _point(pid: str, path: str) -> dict:
    return {
        "id": pid,
        "payload": {
            "id": pid,
            "path": path,
            "shard": "",
            "mtime": 100,
            "size": 200,
            "indexed_at": "2026-01-01T00:00:00+00:00",
        },
    }


@pytest.fixture
def index_db(tmp_path):
    qdrant = FakeQdrant([[_point("a", "/photos/a.jpg"), _point("b", "/photos/b.jpg")]])
    db = IndexDB(str(tmp_path / "images.db"), qdrant, refresh_interval_seconds=3600)
    try:
        yield db, qdrant
    finally:
        db.close()


def test_init_from_qdrant_populates_table(index_db):
    db, _ = index_db
    assert db.init_from_qdrant() == 2
    assert db.get_by_id("a")["path"] == "/photos/a.jpg"
    assert db.get_by_id("b")["size"] == 200


def test_pick_random_returns_requested_ids(index_db):
    db, _ = index_db
    db.init_from_qdrant()
    ids = db.pick_random(2)
    assert len(ids) == 2
    assert set(ids) == {"a", "b"}


def test_pick_unseen_excludes_seen(index_db):
    db, _ = index_db
    db.init_from_qdrant()
    assert db.pick_unseen(10, {"a"}) == ["b"]


def test_favorite_round_trip(index_db):
    db, _ = index_db
    db.init_from_qdrant()
    db.mark_favorite("a")
    assert db.count_favorites() == 1
    favorites = db.list_favorites()
    assert favorites[0]["id"] == "a"
    assert favorites[0]["favorited_at"]
    db.unmark_favorite("a")
    assert db.count_favorites() == 0


def test_mark_favorite_requires_cached_photo(index_db):
    db, _ = index_db
    db.init_from_qdrant()
    with pytest.raises(ImageNotInCacheError):
        db.mark_favorite("missing")


def test_maybe_refresh_respects_interval(index_db):
    db, qdrant = index_db
    db.init_from_qdrant()
    refreshes = qdrant.refreshes
    assert db.maybe_refresh() is False
    assert qdrant.refreshes == refreshes
    db._last_refresh = time.time() - 4000
    assert db.maybe_refresh() is True
    assert qdrant.refreshes == refreshes + 1


def test_get_by_id_returns_none_for_missing(index_db):
    db, _ = index_db
    db.init_from_qdrant()
    assert db.get_by_id("missing") is None


def test_init_from_qdrant_skips_scroll_when_cache_populated(tmp_path):
    """A populated SQLite cache must survive across IndexDB instances
    without triggering a Qdrant scroll."""
    qdrant_a = FakeQdrant([[_point("a", "/photos/a.jpg"), _point("b", "/photos/b.jpg")]])
    db_path = str(tmp_path / "images.db")
    db_a = IndexDB(db_path, qdrant_a, refresh_interval_seconds=3600)
    assert db_a.init_from_qdrant() == 2
    db_a.close()

    # New Qdrant collection grew a third point; the cached DB doesn't know yet.
    qdrant_b = FakeQdrant(
        [
            [
                _point("a", "/photos/a.jpg"),
                _point("b", "/photos/b.jpg"),
                _point("c", "/photos/c.jpg"),
            ]
        ]
    )
    db_b = IndexDB(db_path, qdrant_b, refresh_interval_seconds=3600)
    try:
        refreshes_before = qdrant_b.refreshes
        # Default call must NOT scroll Qdrant — it just restores from SQLite.
        assert db_b.init_from_qdrant() == 2
        assert qdrant_b.refreshes == refreshes_before
        assert db_b.count_images() == 2
        assert db_b.get_by_id("c") is None
        # `force=True` bypasses the skip and rebuilds from current Qdrant state.
        assert db_b.init_from_qdrant(force=True) == 3
        assert qdrant_b.refreshes == refreshes_before + 1
        assert db_b.get_by_id("c") is not None
    finally:
        db_b.close()


def test_init_from_qdrant_skip_resets_refresh_clock(index_db):
    db, qdrant = index_db
    assert db.init_from_qdrant() == 2
    refreshes = qdrant.refreshes
    db._last_refresh = 0.0  # simulate state right after process restart
    # Skip path must bump the refresh clock so maybe_refresh() doesn't
    # immediately re-scroll right after startup.
    db.init_from_qdrant()
    assert db._last_refresh > 0
    assert qdrant.refreshes == refreshes  # still no scroll


def test_random_picker_delegates_to_index_db():
    import asyncio
    import threading

    from search.random import RandomPicker

    class FakeIndexDB:
        def __init__(self):
            self.called_with = None

        def pick_random(self, n):
            self.called_with = n
            return ["x", "y"]

    db = FakeIndexDB()
    # The sync Playwright session keeps an event loop running in the
    # main thread, which makes asyncio.run() and
    # loop.run_until_complete() unusable there. A private loop on its
    # own thread is immune to whatever the main thread is doing.
    bg_loop = asyncio.new_event_loop()
    bg_thread = threading.Thread(target=bg_loop.run_forever, daemon=True)
    bg_thread.start()
    try:
        future = asyncio.run_coroutine_threadsafe(
            RandomPicker(db).pick(2), bg_loop
        )
        picked = future.result(timeout=5)
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join()
    assert picked == ["x", "y"]
    assert db.called_with == 2


# ---------- pick_random_rows (random shuffle page) ----------

def test_pick_random_rows_returns_full_metadata(tmp_path):
    """pick_random_rows returns dicts with id/path/width/height/etc —
    everything the random page needs to render without going back
    to Qdrant.
    """
    qdrant = FakeQdrant(
        [
            [
                {"id": "a", "payload": {"id": "a", "path": "/photos/a.jpg", "collection": "kpop", "indexed_at": "2026-01-01"}},
                {"id": "b", "payload": {"id": "b", "path": "/photos/b.jpg", "collection": "portrait", "indexed_at": "2026-01-01"}},
            ]
        ]
    )
    db_path = str(tmp_path / "images.db")
    db = IndexDB(db_path, qdrant, refresh_interval_seconds=3600)
    try:
        db.init_from_qdrant()
        rows = db.pick_random_rows(2)
        assert len(rows) == 2
        ids = {r["id"] for r in rows}
        assert ids == {"a", "b"}
        # Spot-check the fields the random page reads.
        for r in rows:
            assert r["path"].startswith("/photos/")
            assert "collection" in r
            assert "width" in r
            assert "height" in r
    finally:
        db.close()


def test_pick_random_rows_filters_by_collection(tmp_path):
    """When collections=[...] is passed, only matching rows are sampled."""
    qdrant = FakeQdrant(
        [
            [
                {"id": "a", "payload": {"id": "a", "path": "/a.jpg", "collection": "kpop"}},
                {"id": "b", "payload": {"id": "b", "path": "/b.jpg", "collection": "portrait"}},
                {"id": "c", "payload": {"id": "c", "path": "/c.jpg", "collection": "kpop"}},
            ]
        ]
    )
    db_path = str(tmp_path / "images.db")
    db = IndexDB(db_path, qdrant, refresh_interval_seconds=3600)
    try:
        db.init_from_qdrant()
        rows = db.pick_random_rows(10, collections=["kpop"])
        assert {r["id"] for r in rows} == {"a", "c"}
        # Multi-collection filter: union of the requested libraries.
        rows_multi = db.pick_random_rows(10, collections=["kpop", "portrait"])
        assert {r["id"] for r in rows_multi} == {"a", "b", "c"}
    finally:
        db.close()


def test_pick_random_rows_zero_or_negative_returns_empty(tmp_path):
    """n=0 or n<0 is a no-op, not a crash."""
    qdrant = FakeQdrant([])
    db_path = str(tmp_path / "images.db")
    db = IndexDB(db_path, qdrant, refresh_interval_seconds=3600)
    try:
        assert db.pick_random_rows(0) == []
        assert db.pick_random_rows(-5) == []
    finally:
        db.close()


def test_collection_column_migrated_for_existing_db(tmp_path):
    """A pre-random-shuffle DB without the `collection` column gets
    it added in-place on IndexDB open, same pattern as width/height.
    """
    import sqlite3

    db_path = str(tmp_path / "legacy.db")
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE images (
          id            TEXT PRIMARY KEY,
          path          TEXT NOT NULL,
          shard         TEXT DEFAULT '',
          mtime         INTEGER,
          size          INTEGER,
          indexed_at    TEXT,
          is_favorite   INTEGER DEFAULT 0,
          favorited_at  TEXT
        );
        INSERT INTO images (id, path) VALUES ('legacy1', '/photos/legacy.jpg');
        """
    )
    legacy.commit()
    legacy.close()

    qdrant = FakeQdrant([])
    db = IndexDB(db_path, qdrant, refresh_interval_seconds=3600)
    try:
        # Legacy row is still there, collection defaulted to "".
        cols = {
            row["name"]
            for row in db._conn.execute("PRAGMA table_info(images)").fetchall()
        }
        assert "collection" in cols
        assert db.get_by_id("legacy1")["collection"] == ""
    finally:
        db.close()


# ---------- favorites persistence across cache rebuild ----------

def test_favorites_survive_init_from_qdrant_force_rebuild(tmp_path):
    """A mark_favorite call must persist across a full
    init_from_qdrant(force=True) rebuild. This is the whole point of
    the persistence refactor: user state lives in a separate table
    that the rebuild never touches.
    """
    from PIL import Image

    real = tmp_path / "real.jpg"
    Image.new("RGB", (640, 480), (0, 0, 0)).save(real)

    qdrant = FakeQdrant(
        [
            [
                {"id": "real", "payload": {"id": "real", "path": str(real), "indexed_at": "2026-01-01"}},
            ]
        ]
    )
    db_path = str(tmp_path / "images.db")
    db = IndexDB(db_path, qdrant, refresh_interval_seconds=3600)
    try:
        db.init_from_qdrant()
        db.mark_favorite("real")
        # Favourite recorded.
        row = db.get_by_id("real")
        assert bool(row["is_favorite"])
        assert row["favorited_at"]

        # Force a full rebuild from Qdrant — same data this time.
        db.init_from_qdrant(force=True)
        row = db.get_by_id("real")
        assert bool(row["is_favorite"])
        assert row["favorited_at"]
        assert db.count_favorites() == 1
    finally:
        db.close()


def test_favorite_survives_photo_removal_from_qdrant(tmp_path):
    """If a photo's id disappears from Qdrant (e.g. the file was
    deleted and heal ran), the favourite stays in the table as an
    orphan. It re-attaches automatically if the same id reappears.
    """
    from PIL import Image

    real = tmp_path / "real.jpg"
    Image.new("RGB", (640, 480), (0, 0, 0)).save(real)

    qdrant_present = FakeQdrant(
        [
            [
                {"id": "real", "payload": {"id": "real", "path": str(real), "indexed_at": "2026-01-01"}},
            ]
        ]
    )
    db_path = str(tmp_path / "images.db")
    db = IndexDB(db_path, qdrant_present, refresh_interval_seconds=3600)
    try:
        db.init_from_qdrant()
        db.mark_favorite("real")
        assert db.count_favorites() == 1

        # Qdrant loses the point (operator deleted the source photo,
        # ran heal, etc). The cache rebuilds without it.
        db.qdrant_client = FakeQdrant([])
        db.init_from_qdrant(force=True)

        # No row in `images` for the deleted id...
        assert db.get_by_id("real") is None
        # ...but the favourite row in `favorites` survives.
        assert db.count_favorites() == 0  # 0 because JOIN excludes orphans
        with db._lock:
            row = db._conn.execute(
                "SELECT id FROM favorites WHERE id = ?", ("real",)
            ).fetchone()
        assert row is not None and row["id"] == "real"

        # Re-indexing the same id (same uuid5) re-attaches it.
        db.qdrant_client = qdrant_present
        db.init_from_qdrant(force=True)
        assert db.count_favorites() == 1
        assert bool(db.get_by_id("real")["is_favorite"])
    finally:
        db.close()


def test_favorite_migration_from_legacy_images_columns(tmp_path):
    """A pre-refactor DB with is_favorite/favorited_at on `images`
    gets migrated: rows with is_favorite=1 are copied into the new
    `favorites` table, then the columns are dropped from `images`.
    """
    import sqlite3

    db_path = str(tmp_path / "legacy.db")
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE images (
          id            TEXT PRIMARY KEY,
          path          TEXT NOT NULL,
          shard         TEXT DEFAULT '',
          mtime         INTEGER,
          size          INTEGER,
          indexed_at    TEXT,
          is_favorite   INTEGER DEFAULT 0,
          favorited_at  TEXT
        );
        INSERT INTO images (id, path, is_favorite, favorited_at) VALUES
          ('fav1', '/photos/fav1.jpg', 1, '2026-05-01T00:00:00+00:00'),
          ('fav2', '/photos/fav2.jpg', 1, '2026-05-02T00:00:00+00:00'),
          ('plain', '/photos/plain.jpg', 0, NULL);
        """
    )
    legacy.commit()
    legacy.close()

    qdrant = FakeQdrant([])
    db = IndexDB(db_path, qdrant, refresh_interval_seconds=3600)
    try:
        # Columns are gone from images.
        cols = {row["name"] for row in db._conn.execute("PRAGMA table_info(images)").fetchall()}
        assert "is_favorite" not in cols
        assert "favorited_at" not in cols
        # Favourites table has both favourited rows, no extras.
        fav_rows = db._conn.execute(
            "SELECT id, favorited_at FROM favorites ORDER BY id"
        ).fetchall()
        assert [(r["id"], r["favorited_at"]) for r in fav_rows] == [
            ("fav1", "2026-05-01T00:00:00+00:00"),
            ("fav2", "2026-05-02T00:00:00+00:00"),
        ]
    finally:
        db.close()


def test_favorite_id_set_returns_only_listed_favourites(index_db):
    """favorite_id_set(ids) returns the subset of ids that are favourited.

    Single IN-clause query (Phase C1): 1 SQLite round trip instead
    of N individual get_by_id calls.
    """
    db, _ = index_db
    db.init_from_qdrant()
    # Mark 2 ids as favourites (a and b are the seeded points).
    db.mark_favorite("a")
    db.mark_favorite("b")
    # Ask for the 2 known ids + a missing one; expect a + b back.
    out = db.favorite_id_set(["a", "b", "missing"])
    assert out == {"a", "b"}


def test_favorite_id_set_empty_input_returns_empty_set(tmp_path):
    from unittest.mock import MagicMock

    from search.index_db import IndexDB

    qdrant = MagicMock()
    db = IndexDB(str(tmp_path / "images.db"), qdrant, refresh_interval_seconds=3600)
    assert db.favorite_id_set([]) == set()
