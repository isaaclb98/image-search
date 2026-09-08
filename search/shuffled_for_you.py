"""
search/shuffled_for_you.py — Shuffled For You feed service.

Public API
----------
    build_pool(fav_ids, dis_ids, qdrant, top_pct=1.0, rng=None) -> list[str]
        Materialise the top `top_pct`% of library by relevance-to-taste,
        uniformly shuffled. Cached for `_POOL_TTL_SECONDS` keyed by
        (fav_ids, dis_ids, top_pct); invalidated by
        invalidate_pool_cache().

    rank(..., limit=30, page=0) -> tuple[list[SearchHit], int, bool]
        Slice `limit` ids from `page * limit` of the cached shuffled
        pool, fetch their payloads via qdrant.retrieve_batch, return
        (hits, total, has_more). Re-shuffles on every fresh request
        (per Isaac's design call: "on page reload").

    invalidate_pool_cache() -> None
        Drop the cache. Called from the favorites/dislikes routes
        whenever the user changes their feedback so the next /shuffled
        page sees fresh ranking signal.

Design notes
------------
The pool is built once per request via qdrant.recommend(positive=fav,
negative=dis, limit=pool_size) and then uniform-random shuffled. The
"top top_pct%" semantic is approximate: it is whatever Qdrant returns
for the top-`pool_size` candidates by recommend() score. This is the
fast path (1-3s on 800k library). A separate, slower precomputed-score
path is a future concern.

Cold start (no favorites yet) falls back to a zero-vector search
because Qdrant recommend() requires non-empty positives. The result
degrades to /random-equivalent behaviour for fresh users — see the
zero-vector rank() branch in for_you.py for the same shape.
"""
from __future__ import annotations

import logging
import time as _time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import random

logger = logging.getLogger(__name__)


_POOL_TTL_SECONDS: float = 300.0
"""Default cache TTL for the shuffled pool. 5 min — long enough that
scrolling through one pool across multiple page requests doesn't
re-trigger the Qdrant recommend call, short enough that the pool
eventually evolves as the embedding drift stabilises.

Pool invalidation on like/dislike changes happens via
`invalidate_pool_cache()` (called from the favorites/dislikes
routes) so the TTL is a backstop, not the primary freshness
mechanism."""

DEFAULT_TOP_PCT: float = 1.0
"""Default percentile of library to use as the random pool.
At an 800k library, 1% yields 8000 candidates — fast (~1-3s
recommend) and large enough that uniform shuffle gives real
variety per page refresh."""

SHUFFLED_MAX_LIMIT: int = 100
"""Maximum photos returned per page. Matches the existing
/api/for-you/feed and /api/random caps so the frontend can use
the same PhotoGrid component without bespoke sizing."""

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_pool_cache: dict[
    tuple[tuple[str, ...], tuple[str, ...], float],
    tuple[float, list[str]],
] | None = None


def invalidate_pool_cache() -> None:
    """Drop the cached shuffled pool. Call after every Like/Dislike
    write so the next /shuffled-for-you request sees fresh ranking
    signal. Wired from the favorites and dislikes routes.
    """
    global _pool_cache
    _pool_cache = None


# ---------------------------------------------------------------------------
# Pool construction
# ---------------------------------------------------------------------------


def _library_size(index_db: Any) -> int:
    """Return the current Qdrant point count, or 0 if unreachable.

    Uses the cached index_db.qdrant_point_count() helper rather than
    the live Qdrant API so a transient Qdrant blip doesn't break the
    endpoint. -1 (Qdrant unreachable) is treated as 0 here — the
    caller will short-circuit on an empty pool.
    """
    try:
        n = int(index_db.qdrant_point_count())
    except Exception:  # noqa: BLE001
        logger.warning("shuffled_for_you: library size lookup failed; treating as 0")
        return 0
    return max(n, 0)


def _zero_vector() -> list[float]:
    """Cold-start placeholder query vector. See for_you.py for the
    same shape; duplicated here to keep this module dependency-free
    on the for_you pipeline (so it can be exercised independently
    in tests).
    """
    import os

    from image_search_kernel.registry import get as _registry_get
    from search.for_you_compute import zero_vector

    model_name = os.environ.get("MODEL_NAME", "ViT-L-16-SigLIP2-256")
    return zero_vector(_registry_get(model_name).dim)


def _pool_size_for(library_size: int, top_pct: float) -> int:
    """How many candidates to fetch. Delegates to the same pure helper
    the explore-mode preview uses. Floor of 1 to avoid degenerate
    0-size pools; no ceiling (per Isaac: "no pool ceiling").
    """
    from search.for_you_compute import explore_pool_size

    if library_size <= 0:
        return 0
    return explore_pool_size(library_size, top_pct)


def build_pool(
    *,
    fav_ids: list[str],
    dis_ids: list[str],
    qdrant: Any,
    index_db: Any,
    top_pct: float = DEFAULT_TOP_PCT,
    rng: random.Random | None = None,
) -> list[str]:
    """Return the top `top_pct`% of library by relevance-to-taste,
    uniformly shuffled. Cached for `_POOL_TTL_SECONDS` keyed by
    (fav_ids, dis_ids, top_pct).

    `rng` accepts a `random.Random` instance for deterministic
    shuffling in tests; production callers leave it None and get a
    default Random (which is fine because we always make a fresh
    RNG instance — no global state).
    """
    global _pool_cache
    if _pool_cache is None:
        _pool_cache = {}

    cache_key = (tuple(fav_ids), tuple(dis_ids), float(top_pct))
    now = _time.monotonic()
    cached = _pool_cache.get(cache_key)
    if cached is not None and (now - cached[0]) < _POOL_TTL_SECONDS:
        return list(cached[1])

    library_size = _library_size(index_db)
    pool_size = _pool_size_for(library_size, top_pct)
    if pool_size == 0:
        _pool_cache[cache_key] = (now, [])
        return []

    if fav_ids:
        hits = qdrant.recommend(
            positive=fav_ids,
            negative=dis_ids,
            limit=pool_size,
        )
    else:
        _hits, _ = qdrant.search(
            vector=_zero_vector(),
            limit=pool_size,
        )
        hits = _hits

    ids = [h.id for h in hits]
    rng_obj = rng if rng is not None else __import__("random").Random()
    rng_obj.shuffle(ids)
    _pool_cache[cache_key] = (now, ids)
    return list(ids)


def rank(
    *,
    fav_ids: list[str],
    dis_ids: list[str],
    qdrant: Any,
    index_db: Any,
    limit: int = 30,
    page: int = 0,
    top_pct: float = DEFAULT_TOP_PCT,
    rng: random.Random | None = None,
) -> tuple[list, int, bool]:
    """Slice a page of `limit` items starting at `page * limit` from
    a freshly-shuffled pool. Returns (hits, pool_total, has_more).

    Each call reshuffles by default (no caller-provided session id).
    Per-page slicing reuses the same shuffled pool across `page`
    values within a single call only — the next request gets a
    fresh shuffle. This mirrors /random's behaviour without the
    session cursor (cursor was Isaac's "manual refresh" requirement).

    `limit` is clamped to [1, SHUFFLED_MAX_LIMIT]. `page` is
    clamped to >= 0. `top_pct` is validated (0, 100].
    """
    if limit <= 0:
        limit = 1
    limit = min(limit, SHUFFLED_MAX_LIMIT)
    page = max(page, 0)

    pool = build_pool(
        fav_ids=fav_ids,
        dis_ids=dis_ids,
        qdrant=qdrant,
        index_db=index_db,
        top_pct=top_pct,
        rng=rng,
    )
    pool_total = len(pool)
    if pool_total == 0:
        return [], 0, False

    start = page * limit
    if start >= pool_total:
        return [], pool_total, False
    end = start + limit
    page_ids = pool[start:end]
    has_more = end < pool_total

    hits = qdrant.retrieve_batch(page_ids) if page_ids else []
    # `retrieve_batch` returns hits in input order, but skips missing
    # ids. Re-align to the shuffled pool order so the page renders
    # in walk-through order.
    by_id = {h.id: h for h in hits}
    ordered = [by_id[i] for i in page_ids if i in by_id]
    return ordered, pool_total, has_more
