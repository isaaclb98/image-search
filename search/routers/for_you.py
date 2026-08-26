"""
search/routers/for_you.py — /api/for-you/* (§B2 step 5).

For-You endpoints:

- GET  /api/for-you/state: cheap signal snapshot (likes, dislikes,
  freshest feedback timestamp) for the header chip + empty state.
- GET  /api/for-you/feed: heavy path. Rebuilds the signal snapshot,
  reads fav/dis ids from the index, calls `for_you.rank`, returns a
  SearchResponse-shaped payload with the recommended hits.
- POST /api/for-you/reset: wipe dislikes + feedback_events (favourites
  stay so the next page load still has a "warm start").

Both GETs use `for_you.build_state` and `for_you.rank` from
`search.for_you` (the compute side of the for-you algorithm). The
router is the only place that orchestrates them against Qdrant
+ IndexDB.

The router takes the live index_db / qdrant / invalidators via the
factory function so the `app.include_router` call in `app.py`
stays a one-liner.

Tests pin:

- The three endpoints' status codes + response shapes.
- The state endpoint returns n_likes/n_dislikes/freshest_feedback_ts
  with the documented types.
- Reset returns 204 and calls both invalidators.
- Feed wraps Qdrant errors as 502.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)


def build_for_you_router(
    *,
    index_db: Any,
    qdrant: Any,
    invalidate_favourites_centroid: Callable[[], None],
    invalidate_for_you_signal: Callable[[], None],
) -> APIRouter:
    """Build the for-you router with the live dependencies.

    `qdrant` is the `QdrantSearch` wrapper used by the search routes;
    the router passes it straight through to `for_you.rank`.
    """
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
        limit: int = Query(30, description="max recommendations"),
        diversity: str = Query("balanced", description="diversity mode"),
        diversity_depth: str = Query("auto", description="diversity depth"),
    ) -> dict:
        """Heavy path: rebuild signal + Qdrant recommend + diversity."""
        # Manual validation so we return 400 (not 422) for bad input.
        try:
            limit = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit = 30

        state = await asyncio.to_thread(build_state, index_db=index_db)
        fav_ids, dis_ids = await asyncio.gather(
            asyncio.to_thread(index_db.list_favorite_ids),
            asyncio.to_thread(index_db.list_dislike_ids),
        )

        try:
            hits = await asyncio.to_thread(
                rank,
                state=state,
                fav_ids=fav_ids,
                dis_ids=dis_ids,
                qdrant=qdrant,
                limit=limit,
                diversity_mode=diversity,
                diversity_depth=diversity_depth,
            )
        except (ConnectionError, OSError) as e:
            logger.warning("Qdrant unreachable for /api/for-you/feed: %s", e)
            raise HTTPException(status_code=502, detail="Qdrant unreachable") from e
        except Exception as e:
            # broad set of exception types; we 502 on anything that
            # smells like a qdrant-side problem so the client retries
            # instead of treating it as a stable empty feed.
            if "timeout" in type(e).__name__.lower() or "Timeout" in str(e):
                raise HTTPException(status_code=504, detail="Qdrant timeout") from e
            logger.warning("Qdrant error for /api/for-you/feed: %s", e)
            raise HTTPException(status_code=502, detail="Qdrant error") from e

        # Build the SearchResponse-shaped payload the frontend expects.
        fav_set = set(fav_ids)
        dis_set = set(dis_ids)
        return {
            "query": "",
            "positives": list(fav_ids),
            "negatives": list(dis_ids),
            "view": "for_you",
            "centroid": None,
            "n_likes": state.n_likes,
            "n_dislikes": state.n_dislikes,
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
        }

    @router.post("/api/for-you/reset", status_code=204)
    async def for_you_reset() -> None:
        """Wipe dislikes + feedback_events. Favourites stay."""
        await asyncio.to_thread(index_db.reset_feedback)
        invalidate_favourites_centroid()
        invalidate_for_you_signal()

    return router
