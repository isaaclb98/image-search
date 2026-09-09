"""search/routers/for_you.py — /api/for-you/feed.

GET /api/for-you/feed?top_pct=1&limit=30&page=0&seed=<opaque>:
    Walk through the top `top_pct`% of library ranked by the user's
    taste direction. Like /random but with the pool constrained
    to "points that match your likes minus dislikes."

    The shuffled pool is cached server-side, keyed by
    `(fav_ids, dis_ids, top_pct, seed)`. A new `seed` produces a
    fresh shuffle; reusing the same `seed` walks forward through
    the same shuffle across paginated scroll calls.

Query params
------------
    top_pct : float, default 1.0, range (0, 100].
        Percentile of library to use as the random pool. At 800k
        library: 1% → 8000 candidates, 0.5% → 4000, 5% → 40000.
        No ceiling; large values fetch proportionally more from
        Qdrant (recommend() is the dominant cost: ~1-3s for
        8000, ~5-10s for 40000).

    limit : int, default 30, range [1, FOR_YOU_MAX_LIMIT].
        Max photos returned per page. Matches /api/for-you/feed
        and /api/random so the frontend uses one PhotoGrid.

    page : int, default 0, range [0, ∞).
        Zero-based offset into the cached shuffled pool.

    seed : string, optional.
        Opaque caller-supplied shuffle key. Pass a fresh random
        value on every page reload for a fresh shuffle; reuse
        the same value for paginated scroll calls to walk through
        the same shuffle. Without a seed, all callers share the
        cached shuffle until the TTL expires (legacy behaviour).

Response
--------
    Same SearchResponse shape as /api/random — the frontend can
    use the same render path. `session_id` is None because this
    endpoint doesn't track sessions server-side; `session_total`
    is the pool size so the frontend can show "8000 photos in
    pool".

Cold start
----------
    No favorites yet → falls back to a zero-vector search instead
    of recommend(). The result is the full library ranked by
    relevance-to-zero (effectively random order), which mirrors
    /api/random for fresh users. The endpoint shape and
    semantics are otherwise identical to the recommend() path.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from search.for_you import DEFAULT_FOR_YOU_TOP_PCT, FOR_YOU_MAX_LIMIT, rank_for_you
from search.models import ErrorResponse, SearchResponse

logger = logging.getLogger(__name__)


def _bad_request(detail: str) -> JSONResponse:
    """Build a 400 JSONResponse with the documented error envelope."""
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            error="bad_request", detail=detail, code="bad_request",
        ).model_dump(),
    )


def build_for_you_router(
    *,
    index_db: Any,
    qdrant: Any,
    cfg: Any,
) -> APIRouter:
    """Build the /for-you router with the live dependencies.

    Mirrors the factory pattern used by random.py and for_you.py:
    the router depends only on the runtime objects (qdrant client,
    sqlite index, config), all passed in explicitly. Tests can
    inject fakes via this factory.

    Unlike the for-you router, this one does NOT take a
    `invalidate_*` callback — cache invalidation is wired at the
    favorites/dislikes routes via the same pattern that invalidates
    `search.for_you.invalidate_for_you_cache()`.
    """
    router = APIRouter()
    _web_ui_url = cfg.web_ui_url

    @router.get("/api/for-you/feed", response_model=SearchResponse)
    async def for_you_feed(
        top_pct: Annotated[
            float,
            Query(
                ge=0.001,
                le=100.0,
                description=(
                    "Percentile of library to use as the random pool. "
                    "Default 1.0 (1% → 8000 candidates at 800k library). "
                    "No ceiling; large values fetch proportionally more."
                ),
            ),
        ] = DEFAULT_FOR_YOU_TOP_PCT,
        limit: Annotated[
            int,
            Query(ge=1, le=FOR_YOU_MAX_LIMIT, description="Photos per page."),
        ] = 30,
        page: Annotated[
            int,
            Query(ge=0, description="Zero-based offset into the shuffled pool."),
        ] = 0,
        seed: Annotated[
            str | None,
            Query(
                description=(
                    "Optional opaque string. Frontend passes a fresh "
                    "random value on every page reload so the user "
                    "sees a different shuffle each time; reuse the same "
                    "value for paginated scroll calls within a single "
                    "page-mount to walk through the same shuffle across "
                    "page=0, page=1, ... Different seeds produce different "
                    "shuffles (server-side cached keyed on this value)."
                ),
            ),
        ] = None,
    ) -> SearchResponse:
        """Materialise the top `top_pct`% of library by taste,
        uniformly shuffle, return `limit` ids starting at `page`.

        The shuffle is keyed by `(fav_ids, dis_ids, top_pct, seed)`.
        A client that keeps all four stable across multiple `page`
        values walks through the same shuffle. A new `seed` produces
        a fresh shuffle. If no seed is supplied, all requests share
        the same cached shuffle until the TTL expires.
        """
        try:
            fav_ids, dis_ids = await asyncio.gather(
                asyncio.to_thread(index_db.list_favorite_ids),
                asyncio.to_thread(index_db.list_dislike_ids),
            )
        except Exception:
            logger.exception("for-you: failed to read fav/dis ids")
            return _bad_request("failed to read user signal")  # type: ignore[return-value]

        try:
            page_hits, pool_total, has_more = await asyncio.to_thread(
                rank_for_you,
                fav_ids=fav_ids,
                dis_ids=dis_ids,
                qdrant=qdrant,
                index_db=index_db,
                limit=limit,
                page=page,
                top_pct=top_pct,
                seed=seed,
            )
        except Exception:
            logger.exception("for-you: rank failed")
            return _bad_request("rank failed")  # type: ignore[return-value]

        results = []
        for h in page_hits:
            payload = h.payload or {}
            results.append({
                "id": h.id,
                "path": h.path,
                "score": float(getattr(h, "score", 0.0) or 0.0),
                "url": f"{_web_ui_url.rstrip('/')}/photo/{h.id}/raw" if _web_ui_url else f"/photo/{h.id}/raw",
                "is_favorite": False,
                "is_disliked": False,
                "blurhash": payload.get("blurhash", ""),
                "width": int(payload.get("width", 0) or 0),
                "height": int(payload.get("height", 0) or 0),
            })

        return SearchResponse(
            query="",
            positives=[],
            negatives=[],
            diverse=False,
            view="grid",
            centroid=None,
            centroids=[],
            weights=None,
            results=results,
            took_ms=0,
            offset=page * limit,
            limit=limit,
            has_more=has_more,
            session_id=None,
            session_total=pool_total,
            surprise=False,
        )

    return router
