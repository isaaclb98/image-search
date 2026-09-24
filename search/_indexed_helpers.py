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
import threading
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

    Single IN-clause query (Phase C1): was N individual get_by_id
    calls, now 1. ~10× faster on a 20-tile result page.
    """
    return index_db.favorite_id_set(point_ids)


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


def diversity_page(
    cfg: Any,
    qdrant: Any,
    diversity_cache: Any,
    *,
    vector: list[float],
    effective_limit: int,
    offset: int,
    collections: list[str],
    allowed_ids: list[str] | None,
    favorite_ids: set[str] | None,
    diversity: float,
    pool_depth: int,
) -> tuple[list, bool, Any]:
    """Build or retrieve one complete, stable Diversity ordering.

    Native MMR branch (`test/native-mmr`): Qdrant does the MMR rerank
    server-side via `query_points(mmr=Mmr(diversity, candidates_limit))`.
    This helper wraps that call with:

      1. Cache lookup keyed on (query_vec, diversity, pool_depth, filters).
         Cached hits slice for `?offset` without rerunning MMR.
      2. Optional allowed_ids ∩ favorite_ids intersection when
         `favorites=true` (restricts the candidate pool to favourites).
      3. Post-MMR client-side filters: dhash collapse and relevance floor
         — Qdrant's native MMR doesn't do either, and they matter for
         quality (see `references/diversity-performance.md`).
      4. Stats envelope (`DiversityStats`) with `mmr_source: "qdrant_native"`.
    """
    from search._result_helpers import diversity_metadata as _diversity_metadata
    from search.diversity import DiversityStats
    from search.diversity_compute import (
        _collapse_duplicate_indices,
        apply_relevance_floor,
    )

    cache_key = diversity_cache_key(
        cfg,
        vector, diversity, pool_depth, collections, allowed_ids, favorite_ids,
    )
    cached = diversity_cache.get(cache_key)
    if cached is not None:
        hits = list(cached.hits)
        page = hits[offset:offset + effective_limit]
        return page, len(hits) > offset + effective_limit, _diversity_metadata(cached.stats)

    search_allowed_ids = allowed_ids
    if favorite_ids is not None:
        favorite_list = sorted(favorite_ids)
        if search_allowed_ids is None:
            search_allowed_ids = favorite_list
        else:
            favorite_set = set(favorite_list)
            search_allowed_ids = [
                point_id for point_id in search_allowed_ids
                if point_id in favorite_set
            ]
        if not search_allowed_ids:
            stats = DiversityStats(
                requested=True, applied=True,
                diversity=diversity,
                pool_depth=0,
            )
            return [], False, _diversity_metadata(stats)

    # Payload-first reordering.
    #
    # The collapse that drives diversity (content_sha256 + dhash) needs only
    # the payload fields `dhash` and `content_sha256` — it does NOT need the
    # 1152-dim vector. Fetching vectors for the full pool before collapsing
    # wastes most of the wire transfer: at depth 5000, ~40% of candidates
    # collapse away, so we re-download 40% of vectors we immediately discard.
    #
    # Reordered pipeline:
    #   1. payload-only top-K from Qdrant (returns hits with payload, no vectors)
    #   2. dhash/content_sha256 collapse on the payload-only hits (no transfer)
    #   3. vectors for the surviving ids only (~60% of the original transfer)
    #   4. rank_diverse over the (hit, vector) pairs — same contract as before.
    #
    # This preserves the diversity ranking byte-for-byte (verified: 24/24 top
    # agreement with the original fetch-with-vectors path on prod Qdrant) while
    # cutting the fetch step from ~2300ms to ~150ms at depth 5000.
    pool_depth = _pool_depth_for(cfg, diversity, pool_depth)
    hits = qdrant.search_with_native_mmr(
        vector,
        limit=pool_depth,
        diversity=diversity,
        candidates_limit=pool_depth,
        collections=collections or None,
        allowed_ids=search_allowed_ids,
    )
    if not hits:
        stats = DiversityStats(
            requested=True, applied=True,
            diversity=diversity,
            pool_depth=pool_depth,
        )
        diversity_cache.put(cache_key, [], stats)
        return [], False, _diversity_metadata(stats)

    # Collapse runs against the payload-only hits — only dhash and
    # content_sha256 are read, both indexed payload fields. `query_scores`
    # is the relevance signal (lower scores mean "less similar to the
    # query") and breaks ties when multiple candidates collapse to one
    # representative.
    query_scores = [float(h.score) for h in hits]
    keep_indices = _collapse_duplicate_indices(
        hits,
        query_scores=query_scores,
        duplicate_hamming_distance=cfg.diversity_duplicate_hamming_distance,
    )
    hits = [hits[i] for i in keep_indices]
    query_scores = [query_scores[i] for i in keep_indices]

    # Post-MMR client-side relevance floor: only candidates within the
    # top-relevance band can win. Without it, MMR could surface a
    # weak-but-diverse candidate over a strong-relevant-and-similar one.
    floor_indices = apply_relevance_floor(
        hits,
        query_scores=query_scores,
        floor=cfg.diversity_relevance_floor,
        min_results=min(effective_limit, max(1, len(hits))),
    )
    if len(floor_indices) < len(hits):
        hits = [hits[i] for i in floor_indices]
        query_scores = [query_scores[i] for i in floor_indices]

    stats = DiversityStats(
        requested=True, applied=True,
        diversity=diversity,
        candidate_count=pool_depth,
        result_count=len(hits),
        pool_depth=pool_depth,
    )
    diversity_cache.put(cache_key, hits, stats)
    page = hits[offset:offset + effective_limit]
    return page, len(hits) > offset + effective_limit, _diversity_metadata(stats)


def _pool_depth_for(cfg: Any, diversity: float, requested: int) -> int:
    """Resolve the candidate-pool depth used by the diverse re-ranker.

    On the native MMR branch, pool depth is a free numeric — there
    is no per-mode table. The `diversity` argument is kept for
    backwards-compat with the call site signature but unused for
    override lookups; if `requested > 0` (the user passed a depth),
    we use it as-is; otherwise we fall back to the configured default.
    """
    if requested > 0:
        return requested
    return getattr(cfg, "diversity_default_pool_depth", 5000)


def _digest_values(values) -> str:
    """SHA-256 digest of a list/set/None of strings — used to hash
    request-shape inputs into the diversity cache key.

    Stable order (sorted) + UTF-8 replacement, so equivalent
    inputs in any order produce the same key. None maps to
    a fixed sentinel so the absence of a filter is still part
    of the cache key.
    """
    import hashlib
    digest = hashlib.sha256()
    if values is None:
        digest.update(b"<none>\0")
    for value in sorted(str(item) for item in (values or [])):
        digest.update(value.encode("utf-8", "replace"))
    return digest.hexdigest()


def diversity_cache_key(
    cfg: Any,
    vector: list[float],
    diversity: float,
    pool_depth: int,
    collections: list[str],
    allowed_ids: list[str] | None,
    favorite_ids: set[str] | None,
) -> str:
    """Build the cache key for one Diversity ordering request.

    The key includes the collection name, the diversity float, the
    pool depth, a digest of the query vector, and digests of the
    filter inputs. Two requests that produce identical Diversity
    rankings must hash to the same key.
    """
    import hashlib
    vector_digest = hashlib.sha256(
        repr(tuple(round(float(value), 8) for value in vector)).encode("ascii")
    ).hexdigest()[:20]
    return "|".join((
        cfg.qdrant_collection,
        f"diversity={diversity:.4f}",
        f"pool_depth={pool_depth}",
        vector_digest,
        _digest_values(collections),
        _digest_values(allowed_ids),
        _digest_values(favorite_ids),
    ))


async def materialize_search_page(
    cfg: Any,
    qdrant: Any,
    snapshot_cache: Any,
    *,
    vector: list[float],
    offset: int,
    limit: int,
    collections: list[str],
    allowed_ids: list[str] | None,
    favorite_ids: set[str] | None,
) -> tuple[list, bool]:
    """Return one page of a *stable* ranking, plus `has_more`.

    Drop-in replacement for the plain-search call
    `qdrant.search(vec, limit, offset, ...)`, which is unsound on HNSW:
    each page is ranked at depth `offset+limit`, so neighbouring pages
    disagree near their boundary and return overlapping ids. Measured on
    prod (2.05M points) a walk to offset 480 yielded 78 duplicate ids.

    Instead: freeze one ranking per query and slice pages from it.

      - The snapshot holds `(id, score)` only. Payloads are hydrated per
        page via `retrieve_batch` (~2ms for 24 ids), which keeps
        favourite/dislike flags live rather than frozen for the TTL.
      - The snapshot grows in *bands*. Page 1 fetches a shallow initial
        band (~10ms); deeper pages append bands fetched with
        `exclude_ids` = everything frozen so far. A band is amortised
        over ~20 pages, so per-page cost stays ~10-35ms at any depth,
        versus 179ms/473ms for a single 5k/10k-deep fetch on page 1.
      - Growth stops at `search_max_snapshot_size`; past that
        `has_more` goes False rather than degrading further.

    Returns `(hits, has_more)` where `hits` are `SearchHit` with the
    snapshot's score restored (`retrieve_batch` returns score=0.0).
    """
    from search.search_snapshot import snapshot_key

    cap = cfg.search_max_snapshot_size

    # Never grow a snapshot deeper than the request needs, and never
    # past the cap. An out-of-range offset must not trigger a deep walk.
    if offset >= cap:
        return [], False
    target = min(offset + limit, cap)

    key = snapshot_key(
        collection=cfg.qdrant_collection,
        vector=vector,
        collections=collections,
        allowed_ids=allowed_ids,
        favorite_ids=favorite_ids,
        band_size=cfg.search_band_size,
    )
    snap = await asyncio.to_thread(snapshot_cache.get_or_create, key)

    def _grow_to(wanted: int) -> None:
        """Grow the snapshot until it holds `wanted` ids (or is exhausted).

        Runs under the snapshot's own lock so two concurrent page
        requests for the same query don't fetch the same band twice —
        a racer waits for the in-flight band instead of duplicating it.
        The cache's dict lock is separate and never held across a fetch.
        """
        with snap.lock:
            first_band = len(snap) == 0
            while len(snap) < wanted and not snap.exhausted and len(snap) < cap:
                band = (
                    cfg.search_initial_band_size if first_band
                    else cfg.search_band_size
                )
                first_band = False
                band = min(band, cap - len(snap))
                hits, _ = qdrant.search(
                    vector,
                    band,
                    0,  # always rank from the top of what remains
                    collections or None,
                    allowed_ids,
                    list(snap.ids) or None,
                )
                if not hits:
                    snap.exhausted = True
                    break
                # extend() marks exhausted on a short band, and dedupes
                # locally — so a silently-dropped exclusion filter can
                # never spin this loop (it adds 0, flags exhausted, exits).
                snap.extend(hits, band)

    await asyncio.to_thread(_grow_to, target)

    page_pairs = snap.page(offset, limit)
    snapshot_len = len(snap)
    has_more = (
        snapshot_len > offset + limit
        # Exactly filled the page and Qdrant may have more to give.
        or (not snap.exhausted and snapshot_len < cap)
    )
    if not page_pairs:
        return [], has_more

    page_ids = [pid for pid, _ in page_pairs]
    score_by_id = dict(page_pairs)
    hydrated = await asyncio.to_thread(qdrant.retrieve_batch, page_ids)
    by_id = {h.id: h for h in hydrated}

    # Rebuild in snapshot order. `retrieve_batch` drops ids it can't
    # find (photo pruned mid-session) — skipping them here is what keeps
    # a dead tile from rendering, and has_more is computed from the
    # snapshot rather than the hydrated length so one dropped id can't
    # make the client think the result set ended.
    hits_out: list = []
    for pid in page_ids:
        hit = by_id.get(pid)
        if hit is None:
            continue
        hit.score = score_by_id[pid]
        hits_out.append(hit)

    _prefetch_next_band(
        cfg,
        qdrant,
        snap,
        vector=vector,
        collections=collections,
        allowed_ids=allowed_ids,
        offset=offset,
        limit=limit,
        cap=cap,
    )
    return hits_out, has_more


def _prefetch_next_band(
    cfg: Any,
    qdrant: Any,
    snap: Any,
    *,
    vector: list[float],
    collections: list[str],
    allowed_ids: list[str] | None,
    offset: int,
    limit: int,
    cap: int,
) -> None:
    """Kick off a background fetch of the next band, if one will be needed.

    A cold band fetch costs ~0.5-1s once the exclusion set passes
    ~1750 ids (measured on 2.05M points), which would otherwise be a
    visible hitch roughly every `search_band_size / page_size` pages.
    Serving that cost in the background hides it behind the time the
    user spends looking at the current page — the SPA already prefetches
    two viewports ahead, so the next request usually finds the band
    already frozen.

    Fire-and-forget on a daemon thread: this is pure optimisation, so a
    failure must never affect the response already returned. The snapshot
    it mutates lives in the cache beyond this request's lifetime, which
    is the whole point.
    """
    # Kill-switch. Defaults to off when the attribute is absent so a
    # MagicMock cfg (tests) doesn't accidentally trigger background
    # threads; the real Config sets it True. Tests disable it explicitly
    # for deterministic call-count assertions.
    if not getattr(cfg, "search_prefetch_next_band", False):
        return
    if snap.exhausted or len(snap) >= cap:
        return
    # Only prefetch when the served page consumed the end of what is
    # frozen; otherwise there is slack already available and the next
    # page needs no fetch at all.
    if len(snap) > offset + limit:
        return
    # At most one background band per snapshot. claim_prefetch() is
    # guarded by its own lock, never by snap.lock (which may be held
    # across a long fetch).
    if not snap.claim_prefetch():
        return

    wanted = min(len(snap) + cfg.search_band_size, cap)
    band_size = cfg.search_band_size

    def _run() -> None:
        try:
            with snap.lock:
                while len(snap) < wanted and not snap.exhausted and len(snap) < cap:
                    band = min(band_size, cap - len(snap))
                    hits, _ = qdrant.search(
                        vector,
                        band,
                        0,  # rank from the top of what remains
                        collections or None,
                        allowed_ids,
                        list(snap.ids) or None,
                    )
                    if not hits:
                        snap.exhausted = True
                        break
                    snap.extend(hits, band)
        except Exception:
            # Optimisation only — never propagate to a served response.
            logger.debug("background band prefetch failed", exc_info=True)
        finally:
            snap.release_prefetch()

    threading.Thread(
        target=_run, daemon=True, name="search-band-prefetch"
    ).start()
