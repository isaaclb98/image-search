"""
search/for_you.py

Persistent recommendation service for /for-you.

Public API
----------
    build_state(index_db) -> ForYouState
        Snapshot the signal (counts + freshest feedback timestamp +
        excluded ids). Cheap — does no Qdrant I/O. Called on every
        page render to drive the header chip and the empty-state
        banner.

    rank(state, index_db, qdrant, limit, alpha) -> list[SearchHit]
        Run the recommend → diversity rerank → trim pipeline. Heavy
        path — only called from /api/for-you/feed.

Design
------
We lean on Qdrant's native `recommend()` endpoint rather than
computing our own centroid. Qdrant does `mean(positive_vecs) −
α·mean(negative_vecs)` server-side in 1536-d space, which is exactly
the "rolling recommendation" query we want. The same path /discover
already uses, so future perf tuning reaches both pages at once.

Qdrant's Recommend API doesn't accept per-point weights, so we can't
do exponential time decay inside the vector arithmetic. The MVP
ignores decay — recent and ancient likes both contribute equally.
Phase 2 adds decay by computing the (weighted) centroid in Python and
calling qdrant.search() with it instead.

The diversity step reuses `rank_diverse()` from diversity.py
so the same hit-pool rejection logic applies regardless of source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForYouState:
    n_likes: int
    n_dislikes: int
    freshest_feedback_ts: str | None
    excluded_ids: frozenset[str]


_signal_cache: tuple[float, list[str], list[str]] | None = None
"""In-memory TTL cache for the (fav_ids, dis_ids) pair.

`for_you_feed` runs on every page view of `/for-you` and on every
Home-page render. Each call hit SQLite for two list_*_ids queries
(~60 ms combined on the TrueNAS-backed DB). The signal changes
slowly — the user has to actively Like/Dislike to mutate it — so
a 30-second TTL is more than fresh enough. Tier 2.2.

Keyed by timestamp; cleared when the Like/Dislike handlers call
`invalidate_signal_cache()`. This isn't a re-entrant lock — if a
caller is mid-fetch when invalidation fires they'll get the stale
value until their next call, which is fine for a 30-s window.
"""

import time as _time  # local alias to avoid shadowing the module


def invalidate_signal_cache() -> None:
    """Drop the cached fav/dis ids. Call after every Like/Dislike
    write so the next /for-you request sees the new state.
    """
    global _signal_cache
    _signal_cache = None


def build_state(*, index_db) -> ForYouState:
    """Snapshot the signal from the persistent tables.

    No Qdrant calls — cheap. Use `rank()` for the heavy path.
    """
    global _signal_cache
    now = _time.monotonic()
    if _signal_cache is not None:
        cached_at, cached_fav, cached_dis = _signal_cache
        if now - cached_at < 30.0:
            fav_ids, dis_ids = cached_fav, cached_dis
        else:
            fav_ids = index_db.list_favorite_ids()
            dis_ids = index_db.list_dislike_ids()
            _signal_cache = (now, fav_ids, dis_ids)
    else:
        fav_ids = index_db.list_favorite_ids()
        dis_ids = index_db.list_dislike_ids()
        _signal_cache = (now, fav_ids, dis_ids)
    return ForYouState(
        n_likes=len(fav_ids),
        n_dislikes=len(dis_ids),
        freshest_feedback_ts=index_db.most_recent_feedback(),
        excluded_ids=frozenset(fav_ids + dis_ids),
    )


def _zero_vector() -> list[float]:
    """Zero vector of the active model's dim. Used as a placeholder
    for the cold-start / diversity-rerank query paths that don't
    have a real query vector.

    Thin wrapper that pulls the dim from the registry; the
    pure computation lives in search/for_you_compute.py.
    """
    from image_search_kernel.registry import get as _registry_get
    from search.for_you_compute import zero_vector
    return zero_vector(_registry_get("ViT-gopt-16-SigLIP2-384").dim)


def rank(
    *,
    state: ForYouState,
    fav_ids: list[str],
    dis_ids: list[str],
    qdrant,
    limit: int = 30,
    pool_k: int | None = None,
    diversity_mode: str = "balanced",
    diversity_depth: str = "auto",
) -> list:
    """Recommend + diversity rerank for the for-you feed.

    `limit` is the final returned count. `pool_k` is the recommend
    size before diversity rerank; defaults to `limit * 4` (min 80)
    so diversity has real headroom.

    `fav_ids` and `dis_ids` are passed in by the route handler
    because Qdrant needs them as separate lists but `state` only
    carries a deduplicated exclude set.
    """
    if pool_k is None:
        from search.for_you_compute import pool_k_default
        pool_k = pool_k_default(limit)

    _z = _zero_vector()
    if fav_ids:
        hits = qdrant.recommend(
            positive=fav_ids,
            negative=dis_ids,
            limit=pool_k,
        )
    else:
        # Cold start. qdrant.recommend() requires non-empty positives,
        # so fall back to a zero-vector search then diversity-scatter.
        hits, _ = qdrant.search(
            vector=_z,
            limit=pool_k,
            exclude_ids=list(state.excluded_ids),
        )

    if not hits:
        return []

    try:
        from search.diversity import rank_diverse

        # Fetch (hit, vector) pairs for diversity input.
        # search_with_vectors honours excluded ids and returns what
        # we need without a second qdrant round trip per candidate.
        # We pass the first hit's id (any) as a hint vector — qdrant
        # uses HNSW order rather than the supplied vector for raw
        # search, so the value doesn't matter here.
        hits_with_vecs, _ = qdrant.search_with_vectors(
            vector=_z,
            limit=len(hits),
            exclude_ids=None,
        )

        # Filter to just the ids we already received (search_with_vectors
        # may return a *different* ordering in the worst case; safer to
        # use it as a vector lookup for our hit pool).
        vec_by_id = {h.id: v for h, v in hits_with_vecs}

        pool = [(h, vec_by_id[h.id]) for h in hits if h.id in vec_by_id]
        if not pool:
            return hits[:limit]

        ranking = rank_diverse(
            pool,
            _z,
            mode=diversity_mode,
            max_results=limit,
            depth=diversity_depth,
        )
        return list(ranking.hits)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("diversity rerank failed; using raw order: %s", exc)
        return hits[:limit]
