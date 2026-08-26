"""
search/routers/for_you.py — HTTP routes for the For You feed.

Three endpoints:

- GET /api/for-you/state     → cheap header-chip snapshot
- GET /api/for-you/feed      → page-of-recommendations (round‑13 rewrite)
- POST /api/for-you/reset    → invalidate the cached user signal

The router orchestrates the live `index_db` + `QdrantSearch` against
the pure compute helpers in `search.for_you` and the registry‑aware
zero‑vector builder in `search.for_you_compute`.

Tests pin:
- status codes + response shapes for each endpoint
- feed wraps Qdrant errors as 502
- pagination uses server‑side offset + a one‑row probe for `has_more`
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

_MODEL_NAME = os.environ.get("MODEL_NAME", "ViT-L-16-SigLIP2-256")


def build_for_you_router(
    *,
    index_db: Any,
    qdrant: Any,
    invalidate_favourites_centroid: Callable[[], None],
    invalidate_for_you_signal: Callable[[], None],
) -> APIRouter:
    """Build the for-you router with the live dependencies."""
    from search.for_you import build_state, rank

    router = APIRouter()

    @router.get("/api/for-you/state")
    async def for_you_state() -> dict:
        """Cheap signal snapshot for the header chip and empty-state."""
        state = await asyncio.to_thread(build_state, index_db=index_db)
        return {
            "n_likes": state.n_likes,
            "n_dislikes": state.n_dislikes,
            "freshest_feedback_ts": state.freshest_feedback_ts,
        }

    @router.get("/api/for-you/feed")
    async def for_you_feed(
        limit: int = Query(30, ge=1, le=100, description="max recommendations per page"),
        page: int = Query(0, ge=0, description="zero-based page index"),
        diversity: str = Query("balanced", description="diversity mode"),
        diversity_depth: str = Query("auto", description="diversity depth"),
    ) -> dict:
        """Paginated, server-side for-you feed.

        - Same user signal ⇒ same ranked batch on every reload
          (deterministic top‑K — round‑12).
        - Diversity knob is the only thing that changes ordering.
        - `diversity=off` short-circuits the Python MMR rerank and
          asks Qdrant directly for the slice, saving a round‑trip.
        - Cold start (no favourites) falls back to a zero‑vector
          search so the page is never empty.
        - Pagination is server-side: we ask for `limit + 1` rows so
          we can flag `has_more` cheaply (one extra row means at
          least one more page exists).
        """
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit = 30
        page = max(0, int(page))

        state = await asyncio.to_thread(build_state, index_db=index_db)
        fav_ids, dis_ids = await asyncio.gather(
            asyncio.to_thread(index_db.list_favorite_ids),
            asyncio.to_thread(index_db.list_dislike_ids),
        )

        probe_size = limit + 1

        if diversity == "off":
            # Short-circuit: skip the Python MMR entirely. Pull the
            # ordered slice straight from Qdrant using server‑side
            # offset + score_threshold.
            if fav_ids:
                try:
                    hits = await asyncio.to_thread(
                        qdrant.recommend,
                        positive=fav_ids,
                        negative=dis_ids,
                        limit=probe_size,
                        offset=page * limit,
                        score_threshold=0.30,
                        exclude_ids=list(state.excluded_ids),
                    )
                except (ConnectionError, OSError) as e:
                    logger.warning("Qdrant unreachable for /api/for-you/feed: %s", e)
                    raise HTTPException(status_code=502, detail="Qdrant unreachable") from e
                except Exception as e:  # noqa: BLE001
                    if "timeout" in type(e).__name__.lower() or "Timeout" in str(e):
                        raise HTTPException(status_code=504, detail="Qdrant timeout") from e
                    logger.warning("Qdrant error for /api/for-you/feed: %s", e)
                    raise HTTPException(status_code=502, detail="Qdrant error") from e
            else:
                # Cold start: no likes yet. Fall back to a zero‑vector
                # search so the page is never empty for a fresh user.
                from image_search_kernel.registry import get as _registry_get
                from search.for_you_compute import zero_vector

                _dim = _registry_get(_MODEL_NAME).dim
                try:
                    _hits, _ = await asyncio.to_thread(
                        qdrant.search,
                        vector=zero_vector(_dim),
                        limit=probe_size,
                        offset=page * limit,
                        collections=None,
                        allowed_ids=None,
                        exclude_ids=list(state.excluded_ids),
                    )
                    hits = _hits
                except (ConnectionError, OSError) as e:
                    logger.warning("Qdrant unreachable for /api/for-you/feed: %s", e)
                    raise HTTPException(status_code=502, detail="Qdrant unreachable") from e
                except Exception as e:  # noqa: BLE001
                    if "timeout" in type(e).__name__.lower() or "Timeout" in str(e):
                        raise HTTPException(status_code=504, detail="Qdrant timeout") from e
                    logger.warning("Qdrant error for /api/for-you/feed: %s", e)
                    raise HTTPException(status_code=502, detail="Qdrant error") from e
        else:
            # Python diversity rerank runs MMR over a pool sized to
            # `pool_size`. To detect the end of the corpus cheaply we
            # ask for `probe_size + page * limit` rows (capped at 200
            # to bound the O(n²) cost), then slice to the page.
            pool_size = min(max(probe_size + page * limit, 80), 200)
            try:
                hits = await asyncio.to_thread(
                    rank,
                    state=state,
                    fav_ids=fav_ids,
                    dis_ids=dis_ids,
                    qdrant=qdrant,
                    limit=pool_size,
                    diversity_mode=diversity,
                    diversity_depth=diversity_depth,
                )
            except (ConnectionError, OSError) as e:
                logger.warning("Qdrant unreachable for /api/for-you/feed: %s", e)
                raise HTTPException(status_code=502, detail="Qdrant unreachable") from e
            except Exception as e:  # noqa: BLE001
                if "timeout" in type(e).__name__.lower() or "Timeout" in str(e):
                    raise HTTPException(status_code=504, detail="Qdrant timeout") from e
                logger.warning("Qdrant error for /api/for-you/feed: %s", e)
                raise HTTPException(status_code=502, detail="Qdrant error") from e
            start = page * limit
            hits = hits[start : start + probe_size]

        has_more = len(hits) > limit
        if has_more:
            hits = hits[:limit]

        fav_set = set(fav_ids)
        dis_set = set(dis_ids)
        return {
            "results": [
                {
                    "id": h.id,
                    "path": h.path,
                    "score": float(getattr(h, "score", 0.0)),
                    "url": f"/photo/{h.id}/raw",
                    "blurhash": (h.payload or {}).get("blurhash"),
                    "is_favorite": h.id in fav_set,
                    "is_disliked": h.id in dis_set,
                }
                for h in hits
            ],
            "has_more": has_more,
            "page": page,
        }

    @router.post("/api/for-you/reset", status_code=204)
    async def for_you_reset() -> None:
        """Invalidate the cached user signal + favourites centroid."""
        invalidate_for_you_signal()
        invalidate_favourites_centroid()

    return router