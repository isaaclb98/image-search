"""
search/routers/centroids_list.py — /api/centroids (list) (§B2 step 11).

GET /api/centroids:
    List all centroids currently loaded from CENTROIDS_DIR plus
    the dynamic ones (currently just `favourites`). Each entry
    includes model/dim metadata so the UI can render "expected
    vs loaded" mismatches if a future debug view needs it.

Dynamic centroids are returned alongside the static ones in a
separate `dynamic_centroids` list so the UI can render them in
their own section.

The list endpoint is self-contained (no Qdrant calls) — only
needs the centroid_store and dynamic_centroids registry handles.
The /api/centroids/{name}/search route is a much bigger extraction
(it shares the dynamic-centroid registry + two-layer near-dup
filter with /api/search) and stays inline in `app.py` for now.

Tests pin:
- The endpoint returns the documented JSON shape.
- A missing centroid_store degrades to empty static lists.
- A missing dynamic_centroids registry degrades to empty dynamic lists.
- Dynamic entries include the live cached `n_images` count.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)


def build_centroids_list_router(
    *,
    centroid_store: Any,
    dynamic_centroids: Any,
) -> APIRouter:
    """Build the centroids-list router with the live handles.

    `centroid_store` is the in-memory static-centroid store; may
    be None when the store hasn't been initialised (e.g. tests
    that don't need it). `dynamic_centroids` is the runtime
    centroid registry; may also be None.
    """
    router = APIRouter()

    @router.get("/api/centroids")
    async def list_centroids() -> dict:
        """List static + dynamic centroids with metadata."""
        if centroid_store is None:
            static: list = []
            expected_model = None
            expected_feature_dim = None
        else:
            static = [c.as_dict() for c in centroid_store.list()]
            expected_model = centroid_store.expected_model()
            expected_feature_dim = centroid_store.expected_feature_dim()
        dynamic: list = []
        if dynamic_centroids is not None:
            for spec in dynamic_centroids.list():
                # Trigger a compute (cached) so the API response
                # includes the real n_images count rather than None.
                dynamic_centroids.get_vector(spec.name)
                dynamic.append(
                    spec.public_dict(
                        dynamic_centroids.cached_n_images(spec.name),
                    )
                )
        return {
            "centroids": static,
            "dynamic_centroids": dynamic,
            "expected_model": expected_model,
            "expected_feature_dim": expected_feature_dim,
        }

    return router
