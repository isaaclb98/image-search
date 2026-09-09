"""search/routers/for_you.py — /api/for-you/feed.

GET /api/for-you/feed?top_pct=1&limit=30&page=0:
    A shuffled walk through the top `top_pct`% of library ranked
    by the user's taste direction. Like /random but with the pool
    constrained to "points that match your likes minus dislikes."

    Each request materialises a fresh shuffled pool and returns
    `limit` ids starting at `page * limit`. The pool is cached for
    5 minutes per (fav_ids, dis_ids, top_pct) tuple so a single
    page that walks through multiple `page` values within 5 min
    gets the same shuffle (otherwise each call reshuffles —
    matches /random's "reshuffle on refresh" UX without a session
    cursor).

    The motivation is a "personal random" view: photos that the
    user would have liked anyway, but in a random walk so a
    scrolling user sees variety instead of always the same top-30.

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
    of recommend() (Qdrant requires non-empty positives). Result
    is functionally identical to /random for fresh users; taste
    kicks in as soon as they like something.
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
    async def shuffled_for_you_feed(
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
    ) -> SearchResponse:
        """Materialise the top `top_pct`% of library by taste,
        uniformly shuffle, return `limit` ids starting at `page`.

        The pool reshuffles on every fresh request. A client that
        keeps the same `(fav_ids, dis_ids, top_pct)` and walks
        through multiple `page` values within the 5-min cache TTL
        sees the same shuffle across all those calls.
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
