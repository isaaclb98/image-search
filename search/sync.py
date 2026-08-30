"""
search/sync.py — background sync between `images_pending` and `images`.

Round‑14: separates the indexer from the app entirely.

The indexer writes to `images_pending`; the app reads from `images`.
This SyncManager runs as a background asyncio task inside the
backend process and periodically:

  1. scrolls up to `batch_size` points from `images_pending`
  2. upserts them into `images`
  3. deletes them from `images_pending` by id

Result: the two operations are physically isolated. Concurrent
ingestion never contends with concurrent reads.

Safety properties:
- sync is the *only* writer to `images`. the indexer never touches
  the read collection.
- sync is the *only* deleter of `images_pending`. the indexer only
  inserts/upserts there.
- if a sync batch fails halfway through (crash, network blip), the
  points stay in `images_pending` and will be retried next cycle.
  points may briefly appear twice in `images` (once from a previous
  successful sync, once from a retry) — Qdrant's upsert is idempotent
  on point id, so the duplicate write is harmless.
- if `images_pending` doesn't exist yet, the sync is a no‑op.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SyncStats:
    """Counters surfaced via `/api/sync/status`."""
    cycles: int = 0
    points_moved: int = 0
    last_cycle_ts: float = 0.0
    last_error: str | None = None
    is_running: bool = False
    paused: bool = False


class SyncManager:
    """Moves points from `images_pending` → `images` on an interval."""

    def __init__(
        self,
        *,
        qdrant: Any,            # raw qdrant_client.QdrantClient (not the wrapper)
        read_collection: str,
        write_collection: str,
        batch_size: int = 100,
        interval_seconds: float = 5.0,
        index_db: Any = None,   # round‑21: IndexDB instance for SQLite upserts
    ) -> None:
        self.qdrant = qdrant
        self.read_collection = read_collection
        self.write_collection = write_collection
        self.batch_size = batch_size
        self.interval_seconds = interval_seconds
        self.index_db = index_db
        self.stats = SyncStats()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        # Round‑31: cache whether the read collection is known to
        # exist. Avoids one `get_collection` round-trip per cycle
        # after the first ensure. Reset to False if we ever see a
        # 404 on the read collection again (manual delete, etc.).
        self._read_collection_ensured = False

    def _ensure_read_collection(self) -> bool:
        """Make sure `self.read_collection` exists.

        Returns True if the collection existed (or was created) and
        is ready for upserts. Returns False if it can't be created
        (write_collection missing, schema mismatch, etc.) — caller
        should treat that as "skip this cycle".

        Round‑31 bug fix: Qdrant v1.19's upsert returns 404 if the
        target collection doesn't exist (older versions auto-
        created). On a fresh install the write collection exists
        (indexer created it) but the read one doesn't (no one has
        searched yet). Without this method, sync_once logged
        `points_moved=0` and a 404 every 5 seconds forever.
        """
        if self._read_collection_ensured:
            return True
        try:
            self.qdrant.get_collection(self.read_collection)
            self._read_collection_ensured = True
            return True
        except Exception as e:
            # Match both the real server's "Not found" (from the
            # JSON response) AND the in-memory client's lowercase
            # "Collection X not found" ValueError. The "404" check
            # catches the HTTP-status fallbacks too.
            err = str(e).lower()
            if "not found" not in err and "404" not in err:
                raise
            # Read collection is missing — create it from the write
            # collection's schema so the upsert can succeed.
            try:
                write_info = self.qdrant.get_collection(self.write_collection)
            except Exception:  # noqa: BLE001 — any Qdrant error here means "can't sync right now, bail"; covered by the return below
                # Write collection also missing — nothing to sync yet.
                return False
            vectors = getattr(write_info.config.params, "vectors", None)
            if vectors is None:
                # Sparse or unnamed vectors — fall back to recreating
                # without explicit params and let Qdrant pick defaults.
                self.qdrant.create_collection(self.read_collection)
            else:
                self.qdrant.create_collection(
                    self.read_collection,
                    vectors_config={
                        "size": vectors.size,
                        "distance": vectors.distance.value if hasattr(vectors.distance, "value") else vectors.distance,
                    },
                )
            self._read_collection_ensured = True
            logger.info(
                "sync: created missing read collection %r from "
                "write collection %r's schema",
                self.read_collection, self.write_collection,
            )
            return True

    async def start(self) -> None:
        """Launch the background sync task."""
        if self._task is not None and not self._task.done():
            return  # already running
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="sync-pending-to-search")
        logger.info(
            "SyncManager started: %s → %s every %.1fs (batch=%d)",
            self.write_collection, self.read_collection,
            self.interval_seconds, self.batch_size,
        )

    async def stop(self) -> None:
        """Signal the background task to exit and wait for it."""
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("SyncManager did not stop within 10s; cancelling")
                self._task.cancel()
            self._task = None

    def pause(self) -> None:
        """Stop syncing until `resume()` is called. Cycles short-circuit
        and return 0 immediately so the indexer doesn't have to
        contend with this loop for qdrant's HTTP server."""
        if not self.stats.paused:
            logger.info("SyncManager paused (cycle counter stops advancing)")
            self.stats.paused = True

    def resume(self) -> None:
        """Re-enable the sync loop. Next cycle resumes from the
        current pending state — no points are lost."""
        if self.stats.paused:
            logger.info("SyncManager resumed")
            self.stats.paused = False

    async def sync_once(self) -> int:
        """Run one sync cycle synchronously. Returns points moved.

        Exposed so tests can drive a single cycle deterministically.
        Skips immediately when `paused` so the indexer doesn't
        contend with the sync loop for qdrant's HTTP server.
        """
        import time
        if self.stats.paused:
            return 0
        from qdrant_client.http import models as _qm
        self.stats.cycles += 1
        self.stats.is_running = True
        start = time.monotonic()
        try:
            # Does the pending collection exist?
            try:
                self.qdrant.get_collection(self.write_collection)
            except Exception as e:
                # 404 or "Not found" is fine — indexer hasn't created
                # the collection yet.
                if "Not found" in str(e) or "404" in str(e):
                    self.stats.last_error = None
                    self.stats.last_cycle_ts = time.time()
                    return 0
                raise

            # Round‑31: make sure the read collection exists too.
            # Without this, on a fresh install the upsert below
            # returns 404 every cycle and points never move from
            # pending → live.
            if not self._ensure_read_collection():
                self.stats.last_error = None
                self.stats.last_cycle_ts = time.time()
                return 0

            records = []
            offset = None
            while True:
                batch, next_offset = self.qdrant.scroll(
                    collection_name=self.write_collection,
                    limit=self.batch_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
                if not batch:
                    break
                records.extend(batch)
                if next_offset is None:
                    break
                offset = next_offset

            if not records:
                self.stats.last_cycle_ts = time.time()
                return 0

            # Convert `Record` objects → `PointStruct` (the shape
            # `upsert` expects in qdrant‑client ≥1.10).
            points = [
                _qm.PointStruct(
                    id=r.id,
                    vector=r.vector,
                    payload=r.payload or {},
                )
                for r in records
            ]

            # Upsert into the read collection.
            self.qdrant.upsert(
                collection_name=self.read_collection,
                points=points,
                wait=False,  # don't block on fsync — keep the sync snappy
            )

            # Round‑21: also upsert into the local SQLite cache. The
            # frontend endpoints (`/api/random`, `/api/photo/{id}/raw`,
            # etc.) read from this DB. Without this, newly indexed
            # photos only reach qdrant but never show up in the UI.
            if self.index_db is not None:
                try:
                    await asyncio.to_thread(
                        self.index_db.upsert_records, records,
                    )
                except Exception:
                    logger.exception("sqlite upsert during sync failed")

            # Delete from pending by id.
            ids = [r.id for r in records]
            self.qdrant.delete(
                collection_name=self.write_collection,
                points_selector=ids,
                wait=False,
            )

            self.stats.points_moved += len(points)
            self.stats.last_cycle_ts = time.time()
            logger.info(
                "sync cycle #%d moved %d points in %.2fs",
                self.stats.cycles, len(points), time.monotonic() - start,
            )
            return len(points)
        except Exception as e:  # noqa: BLE001
            self.stats.last_error = repr(e)
            logger.warning("sync cycle failed: %s", e)
            # Round‑31: if the read collection disappeared under us
            # (manual delete, Qdrant restart, etc.), reset the cache
            # flag so the next cycle re-creates it. Without this,
            # the sync gets stuck in "404 forever" mode.
            err = str(e).lower()
            if "not found" in err or "404" in err:
                self._read_collection_ensured = False
            return 0
        finally:
            self.stats.is_running = False

    async def _run(self) -> None:
        """Main loop: run a sync cycle every `interval_seconds`."""
        while not self._stop.is_set():
            try:
                await self.sync_once()
            except Exception:
                logger.exception("sync loop crashed; will retry next interval")
            # wait_for raises TimeoutError when the interval elapses;
            # suppress it so control returns to the top of the loop
            # for the next cycle. The coroutine-completes case doesn't
            # raise (it returns), so we only suppress the timeout.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
