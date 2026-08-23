"""
tests/test_indexer_cache.py — indexer/cache.py contract tests.

Phase B4 acceptance criteria (from the refactor plan §B4):
- SQLite round-trip preserves entries
- Concurrent writers don't corrupt (WAL serialises)
- Killing mid-write leaves DB readable (transaction semantics)
- Stale CACHE_VERSION raises typed error (CacheVersionError)
- Collection mismatch raises typed error

The "must be JSON" assertion in the prior implementation is
deliberately dropped — the plan says "the JSON format is no
longer supported".
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def test_save_then_load_round_trip(tmp_path: Path):
    """Phase B4: SQLite round-trip preserves every entry."""
    from indexer.cache import IndexerCache

    cache_path = tmp_path / "cache.db"
    paths = [(tmp_path / f"img_{i}.jpg", f"id_{i}") for i in range(20)]
    for p, _ in paths:
        p.write_bytes(b"x" * 100)

    c = IndexerCache(cache_path, "test")
    for p, point_id in paths:
        stat = p.stat()
        c.add(p, point_id, mtime=int(stat.st_mtime), size=int(stat.st_size))
    c.save()
    c.close()

    c2 = IndexerCache(cache_path, "test")
    assert c2.load()
    for p, point_id in paths:
        assert c2.has(p)
        assert c2._entries[str(p)].id == point_id


def test_stale_version_raises_typed_error_in_strict_mode(tmp_path: Path):
    """Phase B4: stale CACHE_VERSION raises CacheVersionError (strict)."""
    from indexer.cache import (
        CACHE_VERSION,
        CacheVersionError,
        IndexerCache,
    )

    cache_path = tmp_path / "cache.db"
    # Write a cache with a fake future version, then try to load
    # it in strict mode.
    conn = sqlite3.connect(str(cache_path))
    conn.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE entries ("
        "  path TEXT PRIMARY KEY, id TEXT NOT NULL, mtime INTEGER NOT NULL, "
        "  size INTEGER NOT NULL, indexed_at TEXT NOT NULL);"
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('version', '999')"
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('collection', 'test')"
    )
    conn.commit()
    conn.close()

    c = IndexerCache(cache_path, "test")
    # Non-strict: returns False, doesn't raise.
    assert c.load(strict=False) is False
    # Strict: raises CacheVersionError.
    import pytest
    with pytest.raises(CacheVersionError):
        c.load(strict=True)
    # Sanity: CACHE_VERSION is still 1 — we're not silently
    # bumping it to match the on-disk value.
    assert CACHE_VERSION == 1


def test_collection_mismatch_raises_typed_error(tmp_path: Path):
    """Phase B4: collection mismatch raises typed error in strict mode."""
    from indexer.cache import (
        CacheCollectionMismatchError,
        IndexerCache,
    )

    cache_path = tmp_path / "cache.db"
    conn = sqlite3.connect(str(cache_path))
    conn.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('version', '1')"
    )
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('collection', 'other_collection')"
    )
    conn.commit()
    conn.close()

    c = IndexerCache(cache_path, "expected_collection")
    import pytest
    with pytest.raises(CacheCollectionMismatchError):
        c.load(strict=True)


def test_mid_write_crash_leaves_db_readable(tmp_path: Path):
    """Phase B4: a mid-transaction crash leaves the DB on the last
    committed state. We simulate this by writing a transaction,
    then simulating a partial second transaction that never commits.

    SQLite's atomic-commit guarantee (WAL or rollback journal)
    means a connection that begins a transaction and drops
    without COMMIT will roll back at the next connection's
    BEGIN. The previously committed rows are intact.
    """
    from indexer.cache import IndexerCache

    cache_path = tmp_path / "cache.db"
    p1 = tmp_path / "a.jpg"
    p1.write_bytes(b"a")
    p2 = tmp_path / "b.jpg"
    p2.write_bytes(b"b")

    # Commit a single entry, then start a second write that
    # never commits (simulating a crash).
    c = IndexerCache(cache_path, "test")
    stat = p1.stat()
    c.add(p1, "id-a", mtime=int(stat.st_mtime), size=int(stat.st_size))
    c.save()
    c.close()

    # Simulate the crash: open a fresh connection, BEGIN, insert a
    # row, then close without COMMIT. SQLite must roll back.
    conn = sqlite3.connect(str(cache_path))
    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO entries (path, id, mtime, size, indexed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (str(p2), "id-b", 0, 0, "crash"),
    )
    # Don't commit. Close.
    conn.close()

    # Reload — should see only id-a, not the half-written id-b.
    c2 = IndexerCache(cache_path, "test")
    assert c2.load()
    assert c2.has(p1)
    assert not c2.has(p2)
    assert len(c2) == 1


def test_concurrent_writers_serialise(tmp_path: Path):
    """Phase B4: two concurrent IndexerCache instances writing to
    the same path don't corrupt the DB.

    SQLite's WAL mode serialises writers at the file level. The
    second writer either wins (last-write-wins) or sees a busy
    error and the test caller backs off — but the DB is never
    left in a torn state.
    """
    from indexer.cache import IndexerCache

    cache_path = tmp_path / "cache.db"
    p_a = tmp_path / "a.jpg"
    p_a.write_bytes(b"a")
    p_b = tmp_path / "b.jpg"
    p_b.write_bytes(b"b")

    a = IndexerCache(cache_path, "test")
    b = IndexerCache(cache_path, "test")
    a._open()
    b._open()
    stat_a = p_a.stat()
    stat_b = p_b.stat()
    a.add(p_a, "id-a", mtime=int(stat_a.st_mtime), size=int(stat_a.st_size))
    b.add(p_b, "id-b", mtime=int(stat_b.st_mtime), size=int(stat_b.st_size))
    a.save()
    b.save()
    a.close()
    b.close()

    # Read back: at least one of the two writes is durable. The
    # important property is that the DB is openable + readable,
    # not that both writes are visible (SQLite serialises
    # but doesn't merge). The strict assertion is "no
    # sqlite3.DatabaseError on open".
    c = IndexerCache(cache_path, "test")
    assert c.load()
    assert len(c) >= 1  # at least one writer's rows survive
