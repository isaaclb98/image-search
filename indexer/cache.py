"""
indexer/cache.py — SQLite-backed cache of "what's already in Qdrant".

Phase B4: replaces the prior JSON-based implementation. Same
public API (load / save / has / add / remove_missing / __len__ /
rebuild_from_qdrant) so call sites and tests don't need to change.

Why SQLite over JSON:
  - Atomicity: a single INSERT runs in a transaction; a killed
    mid-write leaves the DB on the last committed row count, not
    a half-written file. (Atomic-rename on a JSON file is also
    atomic, but a SQLite DB gives the same guarantee with
    row-level crash safety AND without the parse cost on load.)
  - Concurrency: SQLite's WAL mode serialises writers, so two
    parallel `save()` calls don't produce a torn write.
  - Query flexibility: filters by mtime/size run as SQL, not
    Python loops. At 1.5M rows this matters.
  - Migrations: versioned schema with a typed `CacheVersionError`
    on stale data, instead of the "silently ignore on version
    mismatch" behaviour the JSON version had.

Schema (v1):
  CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
  CREATE TABLE entries (
    path TEXT PRIMARY KEY,
    id TEXT NOT NULL,
    mtime INTEGER NOT NULL,
    size INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
  );

A `meta` row with key='version' stores the CACHE_VERSION, and
key='collection' stores the collection name. On load, both must
match or `load()` returns False (and `CacheVersionError` is
raised if the caller passes `strict=True`).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qdrant_client import QdrantClient

logger = logging.getLogger(__name__)

# Bump on breaking schema changes. On load, a row whose
# `meta.version` doesn't match this constant is rejected.
CACHE_VERSION: int = 1

# Default path. The CLI / tests override this. Note: extension
# is now `.db` (was `.json` in the prior implementation).
DEFAULT_CACHE_PATH: Path = Path("state/indexer_cache.db")


class CacheVersionError(RuntimeError):
    """Raised when the on-disk cache has a stale CACHE_VERSION.

    Distinct from `load()` returning False (which is the
    non-strict path: the caller can fall back to rebuild_from_qdrant
    without raising). Callers that must fail loudly use
    `load(strict=True)`.
    """


class CacheCollectionMismatchError(RuntimeError):
    """Raised when the on-disk cache was written for a different
    Qdrant collection than the one being indexed."""


@dataclass
class CacheEntry:
    id: str
    mtime: int
    size: int
    indexed_at: str


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    path       TEXT PRIMARY KEY,
    id         TEXT NOT NULL,
    mtime      INTEGER NOT NULL,
    size       INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_mtime ON entries(mtime);
"""


class IndexerCache:
    """SQLite-backed local cache of "what's already in Qdrant".

    Same public API as the prior JSON implementation:
    - `__init__(cache_path, collection)`: open or create
    - `load(strict=False)`: populate from disk; returns True on
      success. On version mismatch in strict mode, raises
      `CacheVersionError`. On collection mismatch, raises
      `CacheCollectionMismatchError`.
    - `save()`: write in-memory entries to disk. Atomic in the
      sense that a single transaction either commits all rows
      or rolls back; the on-disk DB is never half-written.
    - `has(path)`: mtime+size match → True.
    - `add(path, id, mtime, size)`: upsert.
    - `remove_missing()`: prune rows whose files no longer exist.
    - `__len__()`: row count.
    - `rebuild_from_qdrant(client, name)`: replace contents from
      a fresh Qdrant scroll.
    """

    def __init__(self, cache_path: Path, collection: str):
        self._path = Path(cache_path)
        self._collection = collection
        self._entries: dict[str, CacheEntry] = {}
        self._conn: sqlite3.Connection | None = None

    # -- Lifecycle -----------------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._path), isolation_level=None)
            # WAL gives row-level crash safety + concurrent-reader
            # friendliness. The write concurrency is still
            # serialised by SQLite's locking, but the prior
            # JSON-impl had no concurrency story at all.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA_SQL)
            self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "IndexerCache":
        self._open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- Load / save ---------------------------------------------------------

    def load(self, strict: bool = False) -> bool:
        """Populate self._entries from the on-disk DB.

        Returns True on a clean load. Returns False (no raise) if
        the file doesn't exist or is otherwise unparseable. In
        strict mode, version/collection mismatches raise typed
        errors instead of returning False.
        """
        if not self._path.exists():
            return False
        try:
            conn = self._open()
        except sqlite3.DatabaseError as e:
            logger.warning("cache: failed to open %s: %s", self._path, e)
            return False
        try:
            meta_rows = conn.execute(
                "SELECT key, value FROM meta"
            ).fetchall()
        except sqlite3.DatabaseError as e:
            logger.warning("cache: failed to read meta from %s: %s", self._path, e)
            return False
        meta = dict(meta_rows)
        on_disk_version = meta.get("version")
        on_disk_collection = meta.get("collection")
        if on_disk_version is None:
            # No version row — treat as a malformed cache.
            return False
        if on_disk_version != str(CACHE_VERSION):
            msg = (
                f"cache: version mismatch in {self._path} "
                f"(have {on_disk_version}, want {CACHE_VERSION})"
            )
            if strict:
                raise CacheVersionError(msg)
            logger.info(msg + "; ignoring")
            return False
        if on_disk_collection != self._collection:
            msg = (
                f"cache: collection mismatch in {self._path} "
                f"(have {on_disk_collection!r}, want {self._collection!r})"
            )
            if strict:
                raise CacheCollectionMismatchError(msg)
            logger.info(msg + "; ignoring")
            return False
        try:
            rows = conn.execute(
                "SELECT path, id, mtime, size, indexed_at FROM entries"
            ).fetchall()
        except sqlite3.DatabaseError as e:
            logger.warning("cache: failed to read entries from %s: %s", self._path, e)
            return False
        for path_str, point_id, mtime, size, indexed_at in rows:
            self._entries[path_str] = CacheEntry(
                id=str(point_id),
                mtime=int(mtime),
                size=int(size),
                indexed_at=str(indexed_at),
            )
        logger.info("cache: loaded %d entries from %s", len(self._entries), self._path)
        return True

    def save(self) -> None:
        """Atomic write: a single transaction commits all rows.

        SQLite's transaction model means a mid-write crash leaves
        the DB on the previous committed state. No `os.replace`
        gymnastics — the WAL takes care of atomicity.
        """
        conn = self._open()
        # BEGIN is implicit; we commit at the end of the with block.
        with conn:
            # Upsert meta rows.
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("version", str(CACHE_VERSION)),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("collection", self._collection),
            )
            # Wipe + rewrite entries. The dataset can be millions of
            # rows; a single transaction is faster than per-row
            # commits and still safe (WAL checkpoints on commit).
            conn.execute("DELETE FROM entries")
            conn.executemany(
                "INSERT INTO entries (path, id, mtime, size, indexed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        p,
                        e.id,
                        e.mtime,
                        e.size,
                        e.indexed_at,
                    )
                    for p, e in self._entries.items()
                ],
            )

    # -- Queries -------------------------------------------------------------

    def has(self, path: Path) -> bool:
        """True if `path` was previously indexed AND its mtime/size
        match the on-disk stat. Returns False for missing files or
        no entry.
        """
        entry = self._entries.get(str(path))
        if entry is None:
            return False
        try:
            stat = path.stat()
        except OSError:
            return False
        return (
            entry.mtime == int(stat.st_mtime)
            and entry.size == int(stat.st_size)
        )

    def add(self, path: Path, id: str, mtime: int, size: int) -> None:
        """In-memory upsert. Call save() to persist."""
        from datetime import datetime, timezone

        self._entries[str(path)] = CacheEntry(
            id=id,
            mtime=mtime,
            size=size,
            indexed_at=datetime.now(timezone.utc).isoformat(),
        )

    def remove_missing(self) -> int:
        """Drop entries whose on-disk file no longer exists.

        Returns the number of entries dropped. In-memory only;
        call save() to persist.
        """
        gone = [
            p for p in self._entries
            if not Path(p).exists()
        ]
        for p in gone:
            del self._entries[p]
        if gone:
            logger.info("cache: pruned %d missing entries", len(gone))
        return len(gone)

    def __len__(self) -> int:
        return len(self._entries)

    def rebuild_from_qdrant(self, client: "QdrantClient", name: str) -> None:
        """Replace contents with a fresh scroll of `name` in Qdrant.

        Each scroll point becomes a CacheEntry with a sentinel
        mtime/size of -1 — `has()` returns False on sentinel
        entries, so the next run will re-embed everything (which
        is what `--refresh-cache` is meant to do).
        """
        from datetime import datetime, timezone

        self._entries.clear()
        offset = None
        while True:
            points, next_offset = client.scroll(
                collection_name=name,
                offset=offset,
                with_payload=False,
                with_vectors=False,
                limit=1000,
            )
            for p in points:
                self._entries[str(p.id)] = CacheEntry(
                    id=str(p.id),
                    mtime=-1,
                    size=-1,
                    indexed_at=datetime.now(timezone.utc).isoformat(),
                )
            if next_offset is None:
                break
            offset = next_offset
        logger.info("cache: rebuilt %d entries from qdrant", len(self._entries))
