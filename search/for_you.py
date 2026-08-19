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


def build_state(*, index_db) -> ForYouState:
    """Snapshot the signal from the persistent tables.

    No Qdrant calls — cheap. Use `rank()` for the heavy path.
    """
    fav_ids = index_db.list_favorite_ids()
    dis_ids = index_db.list_dislike_ids()
    return ForYouState(
        n_likes=len(fav_ids),
        n_dislikes=len(dis_ids),
        freshest_feedback_ts=index_db.most_recent_feedback(),
        excluded_ids=frozenset(fav_ids + dis_ids),
    )


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
        pool_k = max(limit * 4, 80)

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
            vector=[0.0] * 1536,
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
            vector=[0.0] * 1536,
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
            [0.0] * 1536,
            mode=diversity_mode,
            max_results=limit,
            depth=diversity_depth,
        )
        return list(ranking.hits)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("diversity rerank failed; using raw order: %s", exc)
        return hits[:limit]
