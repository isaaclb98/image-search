"""
search/routers/centroids.py — /api/centroids/reload (§B2 step 7).

POST /api/centroids/reload:
    Rescan CENTROIDS_DIR and rebuild the in-memory store. Manual
    on purpose — the search side has no filesystem watcher.

The centroid list / search routes stay in `app.py` for now; they
need the `centroid_store`, `dynamic_centroids`, and the live
`qdrant` instance passed through, and they share enough helper
state with `/api/search` (the same `seed_ids`-based two-layer
near-dup exclusion, the same diversity surface) that splitting
them out is a larger refactor than a single-route extraction.

This file deliberately stays narrow: only the one endpoint that's
self-contained.

Tests pin:
- The endpoint returns `{count, centroids_dir}` on success.
- 503 when the store isn't initialized.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)


def build_centroids_reload_router(*, centroid_store: Any) -> APIRouter:
    """Build the centroid-reload router with the live store.

    `centroid_store` is the in-memory `CentroidStore`; its `load()`
    method rescans CENTROIDS_DIR and returns the count of loaded
    centroids. `centroid_store.centroids_dir` is the directory that
    was scanned, surfaced in the response so the caller can confirm
    what was reloaded.
    """
    router = APIRouter()

    @router.post("/api/centroids/reload")
    async def reload_centroids() -> dict:
        if centroid_store is None:
            raise HTTPException(
                status_code=503, detail="centroid store not initialized",
            )
        count = centroid_store.load()
        return {
            "count": count,
            "centroids_dir": (
                str(centroid_store.centroids_dir)
                if centroid_store.centroids_dir else None
            ),
        }

    return router
