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
    ) -> None:
        self.qdrant = qdrant
        self.read_collection = read_collection
        self.write_collection = write_collection
        self.batch_size = batch_size
        self.interval_seconds = interval_seconds
        self.stats = SyncStats()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

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

    async def sync_once(self) -> int:
        """Run one sync cycle synchronously. Returns points moved.

        Exposed so tests can drive a single cycle deterministically.
        """
        import time
        from qdrant_client.http import models as _qm
        self.stats.cycles += 1
        self.stats.is_running = True
        start = time.monotonic()
        try:
            # Does the pending collection exist?
            try:
                self.qdrant.get_collection(self.write_collection)
            except Exception as e:  # noqa: BLE001
                # 404 or "Not found" is fine — indexer hasn't created
                # the collection yet.
                if "Not found" in str(e) or "404" in str(e):
                    self.stats.last_error = None
                    self.stats.last_cycle_ts = time.time()
                    return 0
                raise

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
            return 0
        finally:
            self.stats.is_running = False

    async def _run(self) -> None:
        """Main loop: run a sync cycle every `interval_seconds`."""
        while not self._stop.is_set():
            try:
                await self.sync_once()
            except Exception:  # noqa: BLE001
                logger.exception("sync loop crashed; will retry next interval")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass  # interval elapsed, run another cycle
