"""search/routers/for_you.py — /api/for-you/feed.

GET /api/for-you/feed?pool_size=2000&limit=30&offset=0&temperature=0.05&seed=<int>&session=<opaque>:
    Walk through a Gumbel-sampled deck of photos in your taste
    neighborhood. The candidate pool is sourced from Qdrant
    `recommend(positive=likes, negative=dislikes)`, which returns
    the photos most relevant to your taste vector. The Gumbel
    draw is biased toward high scores (top of the recommend()
    ranking) but mixes in randomness as the temperature rises.

    The deck is cached server-side for 30 minutes, keyed by
    (fav_ids, dis_ids, temperature, pool_size, seed). A new
    seed produces a fresh deck; the same seed reproduces it.

Round-88. Replaces the previous uniform-shuffle-the-top-1%
implementation. The uniform shuffle destroyed the ranking
signal — the user could not tell the difference from /random.
Gumbel-max preserves the ranking as a controllable knob, and
the temperature slider lets the user pick "curated top-K" (T=0)
versus "random walk through taste" (T=1.0).

Query params
------------
    pool_size : int, default 2000, range [200, 50_000].
        Number of recommend() candidates. The Qdrant 30s
        server-side timeout fires for larger pools on a 2M
        corpus. 2000 covers a taste neighborhood thoroughly;
        larger values widen the search for the user who wants
        more variety.

    limit : int, default 30, ge=1.
        Photos per page. No upper bound — the natural cap is
        `min(limit, pool_size)`.

    offset : int, default 0, ge=0.
        Zero-based offset into the cached Gumbel deck.

    temperature : float, default 0.05, range [0.0, 1.0].
        Gumbel noise scale. T=0 is deterministic top-K (the
        same 30 photos at offset=0 every time); T=1.0 is
        near-uniform over the pool. The home-page Surprise Me
        uses the same parameter so the two sliders feel
        consistent.

    seed : int, optional.
        Random seed for the Gumbel draw. Pass a fresh random
        int on every page reload so the user sees a different
        deck each time. Pass the same value for paginated
        scroll within a single page-mount to walk through
        the same deck. Without a seed, the route generates a
        fresh random int per request.

    session : string, optional.
        Opaque cache key (sha256 of the inputs). Pass back
        with offset=N to walk the same deck without
        recomputing. Server-side cached for 30 minutes.

Response
--------
    Same SearchResponse shape as /api/search. `session_id` is
    the cache key (so the client can paginate by sending it
    back as `session`). `session_total` is the deck size
    (2400 by default).

Cold start
----------
    No favorites yet → 400 returned with a friendly message.
    The previous version fell back to a zero-vector search
    that degraded to /random-equivalent behaviour; the
    Gumbel path has no equivalent fallback because the taste
    signal is undefined. Cold-start users should land on
    /random or the home page until they have at least one
    like.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from search.for_you import filter_live_ids
from search.for_you_deck import (
    DEFAULT_DECK_SIZE as DEFAULT_FOR_YOU_DECK_SIZE,
)
from search.for_you_deck import (
    DEFAULT_POOL_SIZE,
    POOL_SIZE_MAX,
    POOL_SIZE_MIN,
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    ForYouDeckCache,
    build_for_you_deck,
    compute_for_you_cache_key,
)
from search.image_resolver import resolve_url
from search.models import ErrorResponse, SearchResponse, SearchResult
from search.surprise_deck import DEFAULT_TEMPERATURE

logger = logging.getLogger(__name__)


# Process-local deck cache. Module-level so all requests share
# the same in-memory map. Mirrors `SurpriseDeckCache` from
# search/surprise_deck.py but lives in its own module to keep
# the home-page and for-you TTLs independent.
_for_you_deck_cache = ForYouDeckCache()


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

    Cache invalidation is wired at the favorites/dislikes routes
    via `invalidate_for_you_cache()` so the next /api/for-you/feed
    request after a like/dislike change sees a fresh deck.
    """
    router = APIRouter()
    _web_ui_url = cfg.web_ui_url

    @router.get("/api/for-you/feed", response_model=SearchResponse)
    async def for_you_feed(
        pool_size: Annotated[
            int,
            Query(
                ge=POOL_SIZE_MIN,
                le=POOL_SIZE_MAX,
                description=(
                    f"Number of recommend() candidates the Gumbel "
                    f"draw is taken from. Default {DEFAULT_POOL_SIZE}. "
                    f"Range [{POOL_SIZE_MIN}, {POOL_SIZE_MAX}]. "
                    f"Higher = more variety, slower first page."
                ),
            ),
        ] = DEFAULT_POOL_SIZE,
        limit: Annotated[
            int,
            Query(ge=1, description="Photos per page. No upper bound."),
        ] = 30,
        offset: Annotated[
            int,
            Query(
                ge=0,
                description=(
                    "Zero-based offset into the cached Gumbel deck. "
                    "Pass the same `session` value to walk forward "
                    "through the same deck without recomputing."
                ),
            ),
        ] = 0,
        temperature: Annotated[
            float,
            Query(
                ge=TEMPERATURE_MIN,
                le=TEMPERATURE_MAX,
                description=(
                    "Gumbel noise scale. T=0 is deterministic top-K "
                    "(curated). T=1.0 is near-uniform random over the "
                    f"pool. Default {DEFAULT_TEMPERATURE}."
                ),
            ),
        ] = DEFAULT_TEMPERATURE,
        seed: Annotated[
            int | None,
            Query(
                description=(
                    "Random seed for the Gumbel draw. Pass a fresh "
                    "random int on every page reload so the user sees "
                    "a different deck each time. Pass the same value "
                    "for paginated scroll within a single page-mount "
                    "to walk through the same deck. If omitted, the "
                    "route generates a fresh random seed per request."
                ),
            ),
        ] = None,
        session: Annotated[
            str | None,
            Query(
                description=(
                    "Opaque cache key from a prior /api/for-you/feed "
                    "response. Pass back with offset=N to walk the "
                    "same deck without recomputing. Server-cached "
                    "for 30 minutes."
                ),
            ),
        ] = None,
    ) -> SearchResponse | JSONResponse:
        """
        Walk through a Gumbel-sampled deck of photos in the user's
        taste neighborhood.

        The candidate pool is sourced from Qdrant
        `recommend(positive=likes, negative=dislikes)`. The
        recommend() results are ordered by score descending, and
        the Gumbel draw biases the deck toward high scores by
        the temperature. At T=0 the deck is the deterministic
        top-K; at T=1.0 the deck is near-uniform random over the
        pool.

        Cache key: `compute_for_you_cache_key(fav_ids, dis_ids,
        temperature, pool_size, seed)`. A client that keeps
        these stable across multiple offsets walks through the
        same deck. A new seed (or a new fav_ids/dis_ids after a
        like/dislike) produces a fresh deck.

        Cold start: 0 favourites returns 400. The /for-you page
        surfaces a "Like a few photos to seed" empty state
        before sending a request.
        """
        try:
            raw_fav_ids, raw_dis_ids = await asyncio.gather(
                asyncio.to_thread(index_db.list_favorite_ids),
                asyncio.to_thread(index_db.list_dislike_ids),
            )
        except Exception:
            logger.exception("for-you: failed to read fav/dis ids")
            return _bad_request("failed to read user signal")

        if not raw_fav_ids:
            # Cold start: no taste signal yet. The previous
            # implementation fell back to a zero-vector search
            # (effectively /random), but the Gumbel path has no
            # meaningful ranking without likes to seed recommend().
            # Tell the client to send the user elsewhere.
            return _bad_request(
                "for-you requires at least one favourite to seed "
                "the taste signal. Like a few photos and try again."
            )

        # Qdrant's recommend() requires every positive/negative id
        # to exist; orphans would 404 the whole call and pin the
        # user to an empty pool for the cache TTL. Strip orphans
        # via retrieve_batch before seeding recommend. (See
        # search/for_you.py:filter_live_ids for the same pattern.)
        try:
            fav_ids, dis_ids = await asyncio.gather(
                asyncio.to_thread(filter_live_ids, raw_fav_ids, qdrant),
                asyncio.to_thread(filter_live_ids, raw_dis_ids, qdrant),
            )
        except Exception:
            logger.exception("for-you: orphan-filter failed; falling back to raw ids")
            fav_ids, dis_ids = raw_fav_ids, raw_dis_ids

        if not fav_ids:
            return _bad_request(
                "all favourites are missing from the index. Like a "
                "few photos that are still in the library and try again."
            )

        # Seed defaults to a fresh random int per request so a
        # user who doesn't pass one gets a fresh deck on every
        # call. The frontend explicitly passes the same seed on
        # pagination requests within a single page-mount to walk
        # the cached deck.
        effective_seed = seed if seed is not None else secrets.randbelow(2**32)

        # When `session` is provided, use it as the cache key
        # directly. Otherwise derive one from the inputs. This
        # mirrors the home-page Surprise Me behaviour: client
        # captures session_id from the first response, sends it
        # back on subsequent pages, server uses it as the lookup
        # key.
        if session:
            cache_key = session
        else:
            cache_key = compute_for_you_cache_key(
                fav_ids=fav_ids,
                dis_ids=dis_ids,
                temperature=temperature,
                pool_size=pool_size,
                seed=effective_seed,
            )

        # Look up the cached deck. If we have a session id (or
        # computed cache_key) and it's in the cache, hydrate
        # from there — no Qdrant call. Otherwise build a fresh
        # deck.
        cached = _for_you_deck_cache.get(cache_key)
        if cached is not None:
            _cached_key, entry = cached
            deck_ids = list(entry.point_ids)
            deck_scores = list(entry.scores)
        else:
            try:
                deck_ids, deck_scores = await asyncio.to_thread(
                    build_for_you_deck,
                    fav_ids=fav_ids,
                    dis_ids=dis_ids,
                    qdrant=qdrant,
                    pool_size=pool_size,
                    deck_size=DEFAULT_FOR_YOU_DECK_SIZE,
                    temperature=temperature,
                    seed=effective_seed,
                )
            except RuntimeError as exc:
                logger.warning("for-you: deck build failed: %s", exc)
                return _bad_request(str(exc))
            except ValueError as exc:
                return _bad_request(str(exc))

            _for_you_deck_cache.put(
                cache_key, deck_ids, deck_scores,
            )

        pool_total = len(deck_ids)
        if pool_total == 0:
            return _bad_request("for-you: empty deck")

        start = offset
        if start >= pool_total:
            page_ids: list[str] = []
            page_scores: list[float] = []
            has_more = False
        else:
            end = start + limit
            page_ids = deck_ids[start:end]
            page_scores = deck_scores[start:end]
            has_more = end < pool_total

        # Hydrate payloads via retrieve_batch. Cached hits don't
        # need re-fetch on pagination, but we still call
        # retrieve_batch to materialise path/blurhash/etc. The
        # retrieve_batch path is O(page_size), not O(pool), so
        # the cost is bounded.
        hits = await asyncio.to_thread(qdrant.retrieve_batch, page_ids) if page_ids else []
        by_id = {h.id: h for h in hits}
        results: list[SearchResult] = []
        for pid, score in zip(page_ids, page_scores, strict=True):
            h = by_id.get(pid)
            if h is None:
                # Id was deleted between deck build and page
                # request; skip it. The deck shape (length)
                # stays consistent with the session_total.
                continue
            payload = h.payload or {}
            results.append(
                SearchResult(
                    id=str(h.id),
                    path=str(h.path),
                    score=float(score),
                    score_str=f"{score:.3f}",
                    url=resolve_url(str(h.id), _web_ui_url),
                    is_favorite=False,
                    is_disliked=False,
                    blurhash=payload.get("blurhash", "") or None,
                    width=int(payload.get("width", 0) or 0),
                    height=int(payload.get("height", 0) or 0),
                )
            )

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
            offset=offset,
            limit=limit,
            has_more=has_more,
            session_id=cache_key,
            session_total=pool_total,
            surprise=False,
        )

    return router


def invalidate_for_you_cache() -> None:
    """Drop all cached Gumbel decks. Call after every Like/Dislike
    write so the next /api/for-you/feed request sees a fresh
    deck reflecting the new taste signal.

    Wired from the favorites and dislikes routes.
    """
    _for_you_deck_cache.clear()
