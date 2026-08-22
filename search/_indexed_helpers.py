"""
search/_indexed_helpers.py — index_db-backed helpers used by /api/search.

These were previously closure-bound to `create_app` (capturing
`index_db`, `_cfg`, `resolve_url`). They're a coherent group:
they all read the search-side IndexDB cache and shape the
result list. Lifting them unblocks the /api/search and
/api/centroids/{name}/search extractions from §B2.

`/api/search` and `/api/centroids/{name}/search` pass the
helpers in as factory parameters; the helpers themselves are
pure functions of `(index_db, cfg, ...args)` so they don't
hold any state.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any
from urllib.parse import urlencode

from search.image_resolver import resolve_url
from search.models import SearchResult
from search.qdrant_client import SearchHit

logger = logging.getLogger(__name__)


def favorite_id_set_sync(index_db: Any, point_ids: list[str]) -> set[str]:
    """Sync: read favourite bit for each id from the IndexDB cache.

    The async wrapper `favorite_id_set` runs this in a thread so
    the SQLite read doesn't block the event loop.
    """
    favorites: set[str] = set()
    for pid in point_ids:
        row = index_db.get_by_id(pid)
        if row and int(row.get("is_favorite") or 0) == 1:
            favorites.add(pid)
    return favorites


async def favorite_id_set(index_db: Any, point_ids: list[str]) -> set[str]:
    """Async wrapper: SQLite read off the event loop."""
    return await asyncio.to_thread(favorite_id_set_sync, index_db, point_ids)


async def results_from_hits(
    index_db: Any,
    *,
    cfg: Any,
    hits: list[Any],
    favorite_ids: set[str] | None = None,
    dislike_ids: set[str] | None = None,
) -> list[SearchResult]:
    """Convert raw Qdrant hits into wire-shape SearchResult entries.

    `favorite_ids` and `dislike_ids` are pre-resolved when the
    caller already has them (avoids a second DB round-trip on the
    hot /api/search path); when omitted, they're resolved in
    parallel via the IndexDB.

    Each hit's `score_str` is formatted to 3 decimals so SSR +
    JS render identically. `blurhash`, `width`, `height` come
    from the Qdrant payload (set at index time).
    """
    if favorite_ids is None or dislike_ids is None:
        ids = [h.id for h in hits]
        fav_set, dis_set = await asyncio.gather(
            favorite_id_set(index_db, ids),
            asyncio.to_thread(index_db.dislike_id_set, ids),
        )
        if favorite_ids is None:
            favorite_ids = fav_set
        if dislike_ids is None:
            dislike_ids = dis_set
    return [
        SearchResult(
            id=h.id,
            path=h.path,
            score=h.score,
            score_str=f"{h.score:.3f}",
            url=resolve_url(h.id, cfg.web_ui_url),
            is_favorite=h.id in favorite_ids,
            is_disliked=h.id in dislike_ids,
            # LQIP from the Qdrant payload (set at index time, T9).
            # None when the point was indexed before blurhash landed.
            blurhash=(h.payload or {}).get("blurhash"),
            # Dimensions for the photo-card caption row (Phase E).
            width=(h.payload or {}).get("width"),
            height=(h.payload or {}).get("height"),
        )
        for h in hits
    ]


async def resolve_filename_filter(
    index_db: Any,
    *,
    cfg: Any,
    pattern: str,
) -> tuple[list[str] | None, str | None]:
    """
    Translate a raw `?filename=` pattern to an `allowed_ids` list
    for Qdrant's `HasId` filter, applying the cardinality guard.

    Returns one of four outcomes (always a 2-tuple):

      (None, None)            — no filter requested (pattern
                                  was empty), OR the guard decided
                                  to skip the filter because
                                  matching ids cover > 50% of
                                  the cache. Caller passes `None`
                                  to qdrant.search meaning "no
                                  HasId filter".
      ([], None)              — pattern matched zero images.
                                  Caller surfaces an empty result
                                  (the user typed
                                  `?filename=nonsense`).
      (list[str], None)       — a usable set of allowed ids.
      (None, "bad_request")   — pattern was syntactically
                                  invalid (caller surfaces a 400
                                  with the validator's message
                                  via `path_token_ids`).

    The cardinality guard is the load-bearing piece: when a
    pattern like `2024` matches 60% of the collection, applying
    `HasId` to the rest actively hurts search quality (HNSW
    has to pick the top-K from a non-uniform distribution AND
    serialise the id list on every request) without meaningfully
    narrowing anything. By dropping the filter above the guard
    threshold we get "full ranking + 0 wasted work", which is
    always at least as good as the filtered ranking.
    """
    if not pattern or not pattern.strip():
        return None, None
    try:
        ids = await asyncio.to_thread(
            index_db.path_token_ids, pattern,
        )
    except ValueError:
        # Pattern failed FTS5 validation (e.g. leading `*`,
        # multi-token input, etc). Surface as a 400 — the
        # validator's message is already user-friendly and
        # includes the offending pattern, so we just signal
        # the route handler to wrap it.
        return None, "bad_request"
    if ids is None:
        # Empty pattern after sanitisation. Same as no filter.
        return None, None
    if not ids:
        # Pattern matched zero images. Distinguish this from
        # "skip the filter" so the caller can render an empty
        # results state with the right message (rather than
        # ranking over the whole collection).
        return [], None
    # Cardinality guard. The guard is symmetric: we apply it
    # whether the filter is super-narrow or super-broad — both
    # are degenerate. The > 0.5 threshold matches the design
    # discussion; below it the filter strictly improves latency
    # and relevance; above it the filter competes with HNSW
    # scoring without adding value.
    total = await asyncio.to_thread(index_db.count_images)
    if total > 0:
        coverage = len(ids) / total
        if coverage > cfg.filename_cardinality_guard:
            logger.info(
                "filename filter %r matched %d/%d (%.1f%%); "
                "skipping HasId per cardinality guard",
                pattern, len(ids), total, coverage * 100,
            )
            return None, None
    return ids, None


def surprise_search(hits: list[SearchHit], k: int) -> list[SearchHit]:
    """Shuffle hits and return up to k. Non-deterministic.

    Used by /api/search's Surprise Me mode — the user gets a
    random sample from the top-N candidates rather than the
    strict ranking. Without this the "Surprise Me" UI is
    indistinguishable from the standard top-K view.
    """
    shuffled = list(hits)
    random.shuffle(shuffled)
    return shuffled[:k]


def search_query_string(
    q: str,
    positives: list[str],
    negatives: list[str],
    collections: list[str],
    view: str = "grid",
    centroid: str | None = None,
    favorites: bool = False,
    centroids: list[str] | None = None,
    weights: list[float] | None = None,
    diverse: bool = False,
    diversity_mode: str = "off",
    diversity_depth: str = "auto",
    filename: str = "",
) -> str:
    """
    Build a multi-value search-state query string for photo back links.

    `view` is included so the user lands back on the same view they
    came from. We omit it when it's the default ('grid') to keep
    canonical URLs clean. Centroid state round-trips via repeated
    `?centroid=` params (with `?weights=` when not all-equal) so the
    "view tile → back" path lands on the same centroid search the
    user came from, not a bare `/`.

    `centroids` is the canonical input — a list of centroid names
    in blend order. The legacy `centroid` param is kept for
    single-centroid call sites and emits one `?centroid=` param.
    When `centroids` is supplied (even length 1), it takes
    precedence — the function never emits the same centroid twice
    from both inputs.

    `filename` round-trips the path-substring filter so the
    "view tile → back" link returns to the same narrowed search
    the user came from. Empty / whitespace-only is omitted so
    canonical URLs stay clean. The filter is single-valued, so a
    plain `?filename=` param (no list).
    """
    params: list[tuple[str, str]] = []
    if q:
        params.append(("q", q))
    params.extend(("positives", p) for p in positives)
    params.extend(("negatives", n) for n in negatives)
    params.extend(("collection", c) for c in collections)
    if centroids is not None:
        params.extend(("centroid", c) for c in centroids)
    elif centroid:
        params.append(("centroid", centroid))
    if weights is not None and any(w != 1.0 for w in weights):
        params.append(("weights", ",".join(str(w) for w in weights)))
    if favorites:
        params.append(("favorites", "true"))
    if filename.strip():
        params.append(("filename", filename.strip()))
    if view and view != "grid":
        params.append(("view", view))
    if diversity_mode and diversity_mode != "off":
        params.append(("diversity", diversity_mode))
        if diversity_depth and diversity_depth != "auto":
            params.append(("diversity_depth", diversity_depth))
    elif diverse:
        # Legacy callers that only know the boolean retain the old
        # URL shape; current search pages emit the explicit mode.
        params.append(("diverse", "true"))
    return urlencode(params)


def normalize_prompt_state(
    cfg: Any,
    q: str,
    positives_raw: list[str],
    negatives_raw: list[str],
):
    """
    Normalize q/positive/negative prompt inputs for search.

    Display text is preserved for response/template echo. Dedupe is
    case-insensitive per side, overlong prompts are dropped, and q is
    appended to positives if it is a usable non-duplicate prompt.
    """
    from search.models import PromptState  # local import to avoid circular dep at module load

    max_prompt_chars = cfg.max_prompt_chars
    max_prompts_total = cfg.max_prompts_total

    effective_q = (q or "").strip()
    positive_keys: set[str] = set()
    negative_keys: set[str] = set()
    positive_entries: list[tuple[str, bool]] = []
    negative_entries: list[tuple[str, bool]] = []

    def add_positive(text: str) -> None:
        prompt = text.strip()
        key = prompt.lower()
        if not prompt or len(prompt) > max_prompt_chars or key in positive_keys:
            return
        positive_keys.add(key)
        positive_entries.append((prompt, True))

    def add_negative(text: str) -> None:
        prompt = text.strip()
        key = prompt.lower()
        if not prompt or len(prompt) > max_prompt_chars or key in negative_keys:
            return
        negative_keys.add(key)
        negative_entries.append((prompt, True))

    for prompt in positives_raw:
        add_positive(prompt)
    if effective_q:
        prompt = effective_q
        key = prompt.lower()
        if len(prompt) <= max_prompt_chars and key not in positive_keys:
            positive_keys.add(key)
            positive_entries.append((prompt, False))
    for prompt in negatives_raw:
        add_negative(prompt)

    remaining = max_prompts_total
    capped_positive_entries = positive_entries[:remaining]
    remaining -= len(capped_positive_entries)
    capped_negative_entries = negative_entries[:remaining]
    positives = [prompt for prompt, _explicit in capped_positive_entries]
    negatives = [prompt for prompt, _explicit in capped_negative_entries]
    return PromptState(
        q=effective_q,
        positives=positives,
        negatives=negatives,
        positive_chips=[
            prompt for prompt, explicit in capped_positive_entries if explicit
        ],
        negative_chips=[
            prompt for prompt, explicit in capped_negative_entries if explicit
        ],
    )
