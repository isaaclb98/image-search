"""
search/routers/system.py — system endpoints (§B2 step 1).

The smallest extractable router group: `/healthz` (the k8s probe)
and `/api/cache/status` (operator visibility into the dual-store
sync). This module establishes the pattern for the rest of §B2
(auth, search, favorites, albums, photos, saved-searches, centroids,
discover, for-you) which land in follow-up PRs.

Pattern:

    from fastapi import APIRouter
    def build_router(*, qdrant, cfg, ...) -> APIRouter:
        router = APIRouter()
        @router.get("/healthz")
        async def healthz(): ...
        return router

`create_app()` calls `app.include_router(build_router(...))` and
hands the live `qdrant`/`cfg` instances via parameters rather than
closures. This makes the router importable in isolation and testable
without a full app.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter


def build_system_router(
    *,
    qdrant: Any,
    cfg: Any,
    index_db: Any,
    path_liveness_cache: dict,
    path_liveness_cache_max: int,
) -> APIRouter:
    """Build the system router with the live dependencies."""
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict:
        # qdrant.healthz() is a sync HTTP call. Running it directly
        # inside this async handler blocks the event loop for the
        # duration of any slow Qdrant response — with
        # WEB_CONCURRENCY=1 that means a single slow healthz call
        # hangs the entire worker. Wrap in asyncio.to_thread so the
        # loop stays free.
        ok = await asyncio.to_thread(qdrant.healthz)
        return {"qdrant": ok, "test_mode": cfg.test_mode}

    @router.get("/api/cache/status")
    async def cache_status() -> dict:
        """Operator visibility into the dual-store sync.

        Returns last refresh timestamp + duration, point counts in
        both stores, drift between them, the liveness cache size +
        cap, and the configured refresh interval / TTL. Drift is
        "unknown" when Qdrant is unreachable (qdrant_count == -1)
        so operators don't see a misleading negative number.
        """
        # IndexDB reads are sync (sqlite3). With WEB_CONCURRENCY=1
        # each would block the event loop for the duration of any
        # slow TrueNAS-backed read. Run them in parallel via
        # asyncio.gather so the whole call takes max(time) instead
        # of sum(time). (Tier 1.1.)
        qdrant_count, index_db_count, last_refresh_ts = await asyncio.gather(
            asyncio.to_thread(index_db.qdrant_point_count),
            asyncio.to_thread(index_db.count_images),
            asyncio.to_thread(index_db.last_refresh_time),
        )
        drift: int | str = (
            "unknown" if qdrant_count < 0 else qdrant_count - index_db_count
        )
        return {
            "last_refresh": last_refresh_ts,
            # last_refresh_duration_ms is a TODO in main; field
            # omitted here to match the existing API surface.
            "qdrant_count": qdrant_count,
            "index_db_count": index_db_count,
            "drift": drift,
            "refresh_interval_seconds": cfg.index_db_refresh_interval_seconds,
            "path_liveness_ttl_seconds": cfg.path_liveness_ttl_seconds,
            "path_liveness_cache_size": len(path_liveness_cache),
            "path_liveness_cache_max": path_liveness_cache_max,
        }

    return router