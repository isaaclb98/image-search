"""
search/routers/centroids_search.py — /api/centroids/{name}/search (§B2 step 15).

GET /api/centroids/{name}/search:
    Search using a loaded centroid as the query vector. Mutually
    exclusive with text prompts (the URL shape carries no prompt
    params, so the only failure mode is an unknown centroid name).

The route hands the request straight to Qdrant for the vector
search; it doesn't touch the text encoder chain. Two non-trivial
behaviours:

1. **Two-layer near-duplicate exclusion for dynamic centroids.**
   When the centroid has `seed_ids` (favourites or album
   members), the results must NOT echo the inputs:
     - Layer 1 — exact-id `must_not` at Qdrant: cheap, kills
       exact-seed matches at the filter level so the over-fetch
       doesn't waste bandwidth.
     - Layer 2 — numpy post-pass on candidate vectors: for each
       candidate, compute its cosine distance to the NEAREST
       seed vector and drop hits tighter than a threshold
       calibrated from the seed set's OWN intra-cluster pairwise
       distances (the "how-close-do-two-versions-of-the-same-photo-get"
       scale for THIS centroid).
   Both layers no-op for static `.pt` centroids (no seed_ids).

2. **Over-fetch 3x for Layer 2 headroom.** We ask Qdrant for
   `effective_limit * 3` candidates so that after Layer 2 drops
   near-dups we still have enough to trim back to `effective_limit`.
   For extreme cases where > 2/3 of the top results are near-dups
   of the seeds we'd under-fill the page — that's accepted as a
   rare edge (the alternative — refetching with a larger limit
   until we have enough — introduces its own failure mode).

Tests pin:
- 404 for unknown centroid names.
- 404 for "loaded but empty" dynamic centroids (the empty-state
  UI distinction).
- 400 for invalid limit/offset (manual validation, not 422).
- `exclude_ids` Layer 1 includes the seed ids when the centroid
  is dynamic.
- Static centroids skip Layer 1 / Layer 2 entirely.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from search._indexed_helpers import resolve_filename_filter, results_from_hits
from search._result_helpers import (
    bad_request,
    coerce_view,
    internal_error,
    parse_collections,
    parse_filename,
    qdrant_timeout,
    qdrant_unreachable,
)
from search.centroids import (
    calibrate_near_dup_threshold,
    filter_near_duplicates,
)
from search.models import SearchResponse, SearchResult

logger = logging.getLogger(__name__)


def build_centroids_search_router(
    *,
    qdrant: Any,
    cfg: Any,
    index_db: Any,
    centroid_store: Any,
    dynamic_centroids: Any,
) -> APIRouter:
    """Build the /api/centroids/{name}/search router.

    Dependencies:
      - `qdrant`: QdrantSearch wrapper.
      - `cfg`:    search Config (limits, default view, web_ui_url).
      - `index_db`: search-side IndexDB cache (filename lookup).
      - `centroid_store`: static centroid store (may be None).
      - `dynamic_centroids`: dynamic centroid registry (may be None).
    """
    router = APIRouter()

    @router.get("/api/centroids/{name}/search", response_model=SearchResponse)
    async def search_by_centroid(
        name: str,
        request: Request,
        limit: int = Query(cfg.top_k_default, description="max results"),
        offset: int = Query(0, description="offset into the full result set"),
    ) -> SearchResponse:
        """Search using a loaded centroid as the query vector."""
        if centroid_store is None:
            raise HTTPException(
                status_code=503, detail="centroid store not initialized",
            )
        # Look up static first; fall back to dynamic (registry does
        # lazy compute + cache). This keeps the route's contract
        # the same regardless of which backend the centroid came from.
        # `centroid_name` echoes the canonical form back to the client
        # (static centroids are stored lowercased, dynamic use the
        # registered name as-is).
        # `seed_ids` is the list of source point ids that fed the
        # centroid (favourite ids / album member ids). Empty for
        # static `.pt` centroids. Drives the two-layer near-dup
        # exclusion below: Layer 1 is the `exclude_ids` server-side
        # filter; Layer 2 is the numpy post-pass on candidate
        # vectors. Both no-op when `seed_ids` is empty (the static
        # case).
        vector: list[float] | None = None
        centroid_name = name
        seed_ids: list[str] = []
        static_spec = centroid_store.get(name)
        if static_spec is not None:
            vector = static_spec.vector
            centroid_name = static_spec.name
        elif dynamic_centroids is not None:
            dyn = dynamic_centroids.get_vector(name)
            dyn_spec = dynamic_centroids.get_spec(name)
            if dyn is not None:
                vector, _, seed_ids = dyn
                if dyn_spec is not None:
                    centroid_name = dyn_spec.name
            else:
                # Distinguish "unknown name" from "known but empty":
                # the empty case surfaces a 404 too (treated as
                # "nothing to search against") so the UI can show its
                # own empty-state copy. Names not registered are
                # caught below.
                if dynamic_centroids.get_spec(name) is None:
                    raise HTTPException(
                        status_code=404,
                        detail=f"centroid {name!r} not loaded",
                    )
                raise HTTPException(
                    status_code=404,
                    detail=f"centroid {name!r} has no data yet",
                )
        else:
            raise HTTPException(
                status_code=404, detail=f"centroid {name!r} not loaded",
            )
        if vector is None:
            raise HTTPException(
                status_code=404, detail=f"centroid {name!r} not loaded",
            )
        # Manual validation (consistent with /api/search shape).
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return bad_request("limit must be an integer")  # type: ignore[return-value]
        if not (1 <= limit <= cfg.top_k_max):
            return bad_request(f"limit must be in [1, {cfg.top_k_max}]")  # type: ignore[return-value]
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            return bad_request("offset must be an integer")  # type: ignore[return-value]
        if offset < 0:
            return bad_request("offset must be >= 0")  # type: ignore[return-value]
        if offset >= cfg.max_results_total:
            return SearchResponse(
                query="",
                positives=[],
                negatives=[],
                view=coerce_view(cfg.default_view),
                centroid=centroid_name,
                results=[], took_ms=0, offset=offset, limit=0, has_more=False,
            )
        effective_limit = min(limit, cfg.max_results_total - offset)
        collections = parse_collections(request)
        filename_pattern = parse_filename(request)
        allowed_ids, fname_err = await resolve_filename_filter(
            index_db, cfg=cfg, pattern=filename_pattern,
        )
        if fname_err == "bad_request":
            return bad_request(
                f"invalid filename pattern {filename_pattern!r}"
            )

        t0 = time.time()
        # `allowed_ids == []` short-circuit (see `/` and `/api/search`
        # for the rationale).
        if allowed_ids is not None and not allowed_ids:
            hits: list = []
            has_more = False
        else:
            # See module docstring for the over-fetch + two-layer
            # near-dup exclusion rationale.
            over_fetch_limit = min(
                effective_limit * 3, cfg.max_results_total - offset,
            )
            try:
                if seed_ids:
                    # Need vectors for the Layer 2 post-pass, so go
                    # through `search_with_vectors` (one extra
                    # `with_vectors=True` per hit, no second
                    # round-trip). The same `exclude_ids` Layer 1
                    # filter rides along.
                    pairs, _ = qdrant.search_with_vectors(
                        vector, limit=over_fetch_limit, offset=offset,
                        collections=collections or None,
                        allowed_ids=allowed_ids,
                        exclude_ids=seed_ids,
                    )
                    # Fetch the seed vectors themselves for the
                    # calibration + post-pass. Orphans (ids whose
                    # photo is gone from Qdrant) are silently
                    # dropped here — `retrieve_batch_with_vectors`
                    # omits missing ids from the response.
                    seed_pairs = qdrant.retrieve_batch_with_vectors(seed_ids)
                    seed_vecs: list[list[float]] = [
                        v for _, v in seed_pairs
                    ]
                    if seed_vecs:
                        threshold = calibrate_near_dup_threshold(seed_vecs)
                        cand_vecs = [vec for _, vec in pairs]
                        keep_mask = filter_near_duplicates(
                            cand_vecs, seed_vecs, threshold,
                        )
                        before_count = len(pairs)
                        kept_pairs = [
                            p for p, keep in zip(pairs, keep_mask, strict=False) if keep
                        ]
                        dropped = before_count - len(kept_pairs)
                        # Trim back to what the user asked for.
                        hits = [h for h, _ in kept_pairs[:effective_limit]]
                        # If anything was dropped, signal `has_more`
                        # so the user knows there might be more
                        # distinct results if they paginate. Also
                        # if we filled the limit and there were more
                        # candidates kept than what we showed.
                        if dropped > 0 or len(kept_pairs) > effective_limit:
                            has_more = True
                        else:
                            # Nothing dropped AND we filled the page
                            # — but Qdrant only gave us `before_count`
                            # candidates, not `over_fetch_limit`, so
                            # we can't use the over-fetched limit
                            # for the standard "hit the limit means
                            # more" heuristic. Use the user's
                            # `effective_limit`: if we filled it,
                            # there may be more; if we didn't, there
                            # isn't.
                            has_more = len(hits) >= effective_limit
                    else:
                        # Seed vectors weren't retrievable (all
                        # orphans) — skip Layer 2 and just trim.
                        # Don't apply exclude_ids post-hoc because
                        # Layer 1 already excluded them server-side.
                        hits = [h for h, _ in pairs[:effective_limit]]
                        has_more = (
                            len(pairs) > effective_limit
                            or len(pairs) >= effective_limit
                        )
                else:
                    # Static centroid (or empty dynamic): no
                    # near-dup exclusion. Original single-shot
                    # search path.
                    hits, has_more = qdrant.search(
                        vector, limit=effective_limit, offset=offset,
                        collections=collections or None,
                        allowed_ids=allowed_ids,
                    )
            except (ConnectionError, OSError) as e:
                logger.warning(
                    "Qdrant unreachable for centroid search: %s", e,
                )
                return qdrant_unreachable(str(e))  # type: ignore[return-value]
            except Exception as e:
                if "timeout" in type(e).__name__.lower() or "Timeout" in str(e):
                    logger.warning("Qdrant timeout: %s", e)
                    return qdrant_timeout(str(e))  # type: ignore[return-value]
                logger.exception("centroid search failed")
                return internal_error(str(e))  # type: ignore[return-value]
        took_ms = int((time.time() - t0) * 1000)
        # Resolve fav/dis for the SearchResult list via the indexed
        # helper. The dynamic-centroid path already pulled the seed
        # vectors from Qdrant; this is the parallel lookup against
        # the IndexDB cache.
        results: list[SearchResult] = await results_from_hits(
            index_db, cfg=cfg, hits=hits,
        )
        return SearchResponse(
            query="",
            positives=[],
            negatives=[],
            view=coerce_view(cfg.default_view),
            centroid=centroid_name,
            results=results,
            took_ms=took_ms,
            offset=offset,
            limit=limit,
            has_more=has_more,
        )

    return router
