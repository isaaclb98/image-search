"""
search/routers/random.py — /api/random (§B2 step 9).

GET /api/random?limit=&collections=&view=:
    Sample `limit` photos at random from the index, optionally
    restricted to one or more collections. Backs the home-page
    "Surprise Me" rail and the /random route in the SPA.

The route draws on `index_db.pick_random_rows` (SQL rowid sample
in the search-side cache) and wraps it in the documented
SearchResponse shape so the grid renders without a special-case
client branch.

Two non-obvious things the route does:

1. **Over-fetch loop** — the picker over-fetches by ~10x with a
   3-attempt retry; combined with the lazy-liveness cache (60 s),
   a small fraction of rows can still come back as "dead" before
   the next periodic refresh. The route loops up to 10 attempts,
   accumulating unique ids, until it has `limit` survivors or the
   collection is genuinely exhausted. Cap is hard-coded to keep
   the latency budget bounded.

2. **No liveness filter** — by the same logic, `_random_rows_to_results`
   deliberately does NOT drop dead-NAS-file rows. The /random UX
   tolerates a broken tile for one cache refresh (60 s); doing
   the same drop-favourites/ do would require a second DB pass per
   request. (Compare with `_favorite_rows_to_results` which DOES
   filter — it can't over-fetch and broken tiles are jarring
   there.)

Tests pin:
- The endpoint returns the documented SearchResponse shape.
- Manual validation of `limit` returns 400 (not 422).
- has_more is True when the page is filled (caller paginates
  with IntersectionObserver).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from search.image_resolver import resolve_url
from search.models import ErrorResponse, SearchResponse, SearchResult

logger = logging.getLogger(__name__)


# Match the inline limit that was in `app.py`. Kept as a module
# constant so callers / tests / docs can reference one source of truth.
RANDOM_MAX_LIMIT = 200

# Number of over-fetch attempts before giving up. Bound protects the
# request latency budget; 10 was the previous in-app cap.
RANDOM_MAX_ATTEMPTS = 10


def _random_rows_to_results(
    rows: list[dict], *, web_ui_url: str,
) -> list[SearchResult]:
    """Build SearchResult objects from SQLite random-sample rows.

    Mirrors `_favorite_rows_to_results` but reads is_favorite from
    the cache and uses the cache's width/height for future masonry
    support. No liveness filter — see module docstring.
    """

    def _maybe_int(v: Any) -> int | None:
        try:
            iv = int(v) if v is not None else None
        except (TypeError, ValueError):
            return None
        return iv if iv and iv > 0 else None

    out: list[SearchResult] = []
    for row in rows:
        is_fav = bool(int(row.get("is_favorite") or 0))
        _bh = row.get("blurhash") or None
        out.append(
            SearchResult(
                id=str(row["id"]),
                path=str(row["path"]),
                score=0.0,
                score_str="",
                url=resolve_url(str(row["id"]), web_ui_url),
                is_favorite=is_fav,
                is_disliked=bool(int(row.get("is_disliked") or 0)),
                width=_maybe_int(row.get("width")),
                height=_maybe_int(row.get("height")),
                blurhash=_bh,
            )
        )
    return out


def _coerce_view(raw: str | None) -> str:
    """Map the `view` query param to a known value.

    Defaults to "grid" when the param is missing or unrecognised;
    the SPA currently renders only the grid but this lets a future
    feed view hook in without a contract change.
    """
    if raw in ("grid", "feed"):
        return raw
    return "grid"


def _bad_request(detail: str) -> JSONResponse:
    """Build a 400 JSONResponse with the documented error envelope."""
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            error="bad_request", detail=detail, code="bad_request",
        ).model_dump(),
    )


def build_random_router(
    *,
    index_db: Any,
    cfg: Any,
) -> APIRouter:
    """Build the random router with the live dependencies."""
    router = APIRouter()

    @router.get("/api/random", response_model=SearchResponse)
    async def api_random(
        request: Request,  # kept for parity with /api/search
        limit: int = Query(cfg.top_k_default, description="max results"),
        collections: Annotated[
            list[str] | None,
            Query(description="restrict to one or more collections; empty = whole set"),
        ] = None,
        view: str = Query(cfg.default_view, description="result view: 'grid' or 'feed'"),
    ) -> SearchResponse:
        view = _coerce_view(view)
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return _bad_request("limit must be an integer")  # type: ignore[return-value]
        if not (1 <= limit <= RANDOM_MAX_LIMIT):
            return _bad_request(f"limit must be in [1, {RANDOM_MAX_LIMIT}]")  # type: ignore[return-value]
        # Clean up the collection list (drop empties, dedupe while
        # preserving order so the response is stable for the client).
        seen: set[str] = set()
        clean_collections: list[str] = []
        for c in collections or []:
            c = (c or "").strip()
            if c and c not in seen:
                seen.add(c)
                clean_collections.append(c)
        try:
            rows: list[dict] = []
            seen_ids: set[str] = set()
            for _ in range(RANDOM_MAX_ATTEMPTS):
                more = await asyncio.to_thread(
                    index_db.pick_random_rows,
                    limit, clean_collections or None,
                )
                for r in more:
                    rid = str(r.get("id"))
                    if rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                    rows.append(r)
                    if len(rows) >= limit:
                        break
                if len(rows) >= limit:
                    break
        except Exception as e:
            logger.exception("random sample failed")
            return JSONResponse(  # type: ignore[return-value]
                status_code=500,
                content=ErrorResponse(
                    error="internal_error",
                    detail=str(e),
                    code="internal_error",
                ).model_dump(),
            )
        results = _random_rows_to_results(rows[:limit], web_ui_url=cfg.web_ui_url)
        # has_more = True when we filled the page (might be more) or
        # when the caller asked for more than the collection holds
        # (everything fits, nothing more). The /random UI uses an
        # IntersectionObserver to append on scroll; the sentinel stays
        # until a fetch returns fewer than `limit` rows, signalling
        # "collection exhausted, stop scrolling".
        has_more = len(results) >= limit
        return SearchResponse(
            query="",
            positives=[],
            negatives=[],
            view=view,
            centroid=None,
            results=results,
            took_ms=0,
            offset=0,
            limit=limit,
            has_more=has_more,
        )

    return router
