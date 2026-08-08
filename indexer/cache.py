"""
indexer/cache.py — local JSON cache of "what's already in Qdrant".

Why this exists: the indexer used to call Qdrant's retrieve-by-id
once per batch (e.g. 3,750 round trips for a 60K folder over HTTPS)
just to check "is this already indexed?". At 50–200ms per call,
that's 6–12 minutes of pure round-trip latency for a no-op re-index.

What it is: a local JSON file keyed by absolute path, value is
{id, mtime, size, indexed_at}. Loaded at startup, looked up in
O(1) during the scan, written back per batch. Lookups are
microseconds; the only Qdrant call is the load (a single scroll)
and the per-batch upsert of new points.

When to invalidate:
  - A file's mtime or size changed → re-embed (covered by `has`).
  - The cache and Qdrant drift (someone deleted points out-of-band):
    use `--refresh-cache` to rebuild from a fresh scroll.
  - Files deleted from disk: leave the entry; `has` returns False
    on a missing stat. The `--prune` mode also sweeps them out.

Bump CACHE_VERSION on any breaking schema change; old caches are
silently ignored on load.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qdrant_client import QdrantClient

logger = logging.getLogger(__name__)

# Bump on breaking schema changes; old caches are silently ignored.
CACHE_VERSION: int = 1

# Default path, relative to cwd. Indexer takes a `--cache-file`
# override; tests pass an explicit tmp path.
DEFAULT_CACHE_PATH: Path = Path("state/indexer_cache.json")


@dataclass
class CacheEntry:
    id: str
    mtime: int
    size: int
    indexed_at: str


class IndexerCache:
    """
    In-memory + on-disk cache of "what's already indexed in
    Qdrant, and what its on-disk signature was at index time".

    Lifecycle:
        cache = IndexerCache(path, collection)
        cache.load()  # or cache.rebuild_from_qdrant(client, name)
        for path in paths:
            if not cache.has(path):
                # ... embed + upsert ...
                cache.add(path, id, mtime, size)
        cache.save()  # atomic write
    """

    def __init__(self, cache_path: Path, collection: str):
        self._path = Path(cache_path)
        self._collection = collection
        self._entries: dict[str, CacheEntry] = {}

    # ---- I/O ----

    def load(self) -> bool:
        """
        Load the cache from disk. Returns True on success, False
        if the file doesn't exist or is invalid (the caller
        should then fall back to per-batch Qdrant checks, or call
        `rebuild_from_qdrant` to populate from Qdrant).
        """
        if not self._path.exists():
            return False
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("cache: failed to load %s: %s", self._path, e)
            return False
        if not isinstance(raw, dict):
            return False
        if raw.get("version") != CACHE_VERSION:
            logger.info(
                "cache: version mismatch in %s (have %s, want %s); ignoring",
                self._path, raw.get("version"), CACHE_VERSION,
            )
            return False
        if raw.get("collection") != self._collection:
            logger.info(
                "cache: collection mismatch in %s (have %r, want %r); ignoring",
                self._path, raw.get("collection"), self._collection,
            )
            return False
        entries = raw.get("entries", {})
        if not isinstance(entries, dict):
            return False
        loaded = 0
        for path_str, entry in entries.items():
            if not isinstance(path_str, str) or not isinstance(entry, dict):
                continue
            try:
                self._entries[path_str] = CacheEntry(
                    id=str(entry["id"]),
                    mtime=int(entry["mtime"]),
                    size=int(entry["size"]),
                    indexed_at=str(entry.get("indexed_at", "")),
                )
                loaded += 1
            except (KeyError, ValueError, TypeError):
                continue
        logger.info("cache: loaded %d entries from %s", loaded, self._path)
        return True

    def save(self) -> None:
        """Atomic write to disk. Creates parent dirs as needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "collection": self._collection,
            "entries": {p: asdict(e) for p, e in self._entries.items()},
        }
        # Atomic write: tmp + rename. Same dir so the rename is on
        # the same filesystem (and thus atomic). On crash mid-write,
        # the existing cache file is untouched.
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".cache-", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=1)
            os.replace(tmp_path, self._path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    # ---- Queries ----

    def has(self, path: Path) -> bool:
        """
        True if `path` was previously indexed AND its mtime/size
        match the on-disk stat (i.e. it hasn't been modified since
        the last index). Returns False for missing files.
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
        self._entries[str(path)] = CacheEntry(
            id=id,
            mtime=mtime,
            size=size,
            indexed_at=datetime.now(timezone.utc).isoformat(),
        )

    def remove_missing(self) -> int:
        """
        Drop entries whose paths no longer exist on disk. Returns
        the number removed. Called by `--prune` to keep the cache
        from accumulating stale entries forever.
        """
        to_drop = [p for p in self._entries if not Path(p).exists()]
        for p in to_drop:
            del self._entries[p]
        return len(to_drop)

    def __len__(self) -> int:
        return len(self._entries)

    # ---- Rebuild from Qdrant ----

    def rebuild_from_qdrant(self, client: QdrantClient, name: str) -> None:
        """
        Build the cache from a Qdrant scroll of every point in the
        collection. Reads only the `path` payload field; other
        payload is ignored. Entries whose paths no longer exist on
        disk are silently dropped (we don't cache entries we can't
        validate against the filesystem).
        """
        self._entries = {}
        next_offset = None
        while True:
            points, next_offset = client.scroll(
                collection_name=name,
                limit=1000,
                offset=next_offset,
                with_payload=["path"],
                with_vectors=False,
            )
            for p in points:
                payload = p.payload or {}
                path_str = payload.get("path", "")
                if not path_str:
                    continue
                try:
                    stat = Path(path_str).stat()
                except OSError:
                    # File's gone (pruned elsewhere or moved). Skip.
                    continue
                self._entries[path_str] = CacheEntry(
                    id=str(p.id),
                    mtime=int(stat.st_mtime),
                    size=int(stat.st_size),
                    indexed_at=datetime.now(timezone.utc).isoformat(),
                )
            if next_offset is None:
                break
