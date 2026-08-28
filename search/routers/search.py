"""
search/routers/search.py — /api/search (§B2 step 40).

GET /api/search:
    The main search endpoint. Composes the text encoder + centroid
    blend + diversity re-rank + filename filter into a single ranked
    result list.

This router is the largest of the B2 extractions (~260 lines).
Most helpers it needs were lifted to module level in steps 13-39.
The two remaining closure-bound helpers are passed in as factory
parameters:
- `resolve_query_vector`: encodes the prompt state (or blends
  centroids) into a single vector. Depends on `text_encoder`,
  `_dynamic_centroids`, `_centroid_store`, `_cfg`.
- `favorite_ids_for_filter`: async wrapper around
  `index_db.list_favorites(...)` capped to `_cfg.max_results_total`.

The router takes both factories as `Any`-typed callable slots so
the existing closure bindings work without further refactoring.
A follow-up commit can lift the two helpers to module level and
replace the factory slots with explicit arguments.

Cache semantics:
- 400 for bad input (manual validation, not 422)
- 200 + ETag for cacheable responses
- 304 for If-None-Match hits
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from search._indexed_helpers import (
    diversity_page,
    normalize_prompt_state,
    resolve_filename_filter,
    results_from_hits,
    surprise_search,
)
from search._result_helpers import (
    bad_request,
    coerce_view,
    internal_error,
    parse_centroids,
    parse_collections,
    parse_filename,
    parse_weights,
    qdrant_timeout,
    qdrant_unreachable,
)
from search.diversity import resolve_depth, resolve_mode
from search.models import DiversityMetadata, SearchResponse

logger = logging.getLogger(__name__)


def build_search_router(
    *,
    qdrant: Any,
    cfg: Any,
    index_db: Any,
    diversity_cache: Any,
    resolve_query_vector: Any,
    favorite_ids_for_filter: Any,
) -> APIRouter:
    """Build the /api/search router.

    Dependencies:
      - `qdrant`: QdrantSearch wrapper.
      - `cfg`:    search Config.
      - `index_db`: search-side IndexDB cache.
      - `diversity_cache`: DiversityResultCache instance.
      - `resolve_query_vector`: closure-bound prompt/centroid
        vector resolver (passed through for now — §B2 step 41
        will lift it to module level).
      - `favorite_ids_for_filter`: closure-bound favorites-set
        resolver (passed through for now — §B2 step 42 will
        lift it to module level).
    """
    router = APIRouter()

    @router.get("/api/search", response_model=SearchResponse)
    async def api_search(
        request: Request,
        q: str = Query("", description="text query"),
        limit: int = Query(cfg.top_k_default, description="max results"),
        offset: int = Query(0, description="offset into the full result set"),
        view: str = Query(cfg.default_view, description="result view: 'grid' or 'feed'"),
        favorites: bool = Query(False, description="restrict results to favourites"),
        diverse: bool = Query(False, description="apply MMR diversity re-ranking"),
        diversity: str | None = Query(
            None, description="Diversity strength: off, low, balanced, or high",
        ),
        diversity_depth: str | None = Query(
            None, description="Diversity candidate depth: auto, 500, 1000, 2000, or 5000",
        ),
        surprise: bool = Query(False, description="Surprise Me — random sample from deep pool"),
    ) -> JSONResponse:
        # Manual validation so we return 400 (not 422) for bad input.
        view = coerce_view(view)
        try:
            diversity_mode, diversity_strength = resolve_mode(diversity, diverse)
        except ValueError as exc:
            return bad_request(str(exc))  # type: ignore[return-value]
        try:
            diversity_depth_mode, diversity_pool_depth = resolve_depth(
                diversity_depth, diversity_mode,
            )
        except ValueError as exc:
            return bad_request(str(exc))  # type: ignore[return-value]
        diverse = diversity_mode != "off"
        if surprise and diverse:
            return bad_request(
                "Diversity cannot be combined with Surprise Me. Choose one search mode."
            )
        prompt_state = normalize_prompt_state(
            cfg,
            q,
            [p.strip() for p in request.query_params.getlist("positives") if p.strip()],
            [p.strip() for p in request.query_params.getlist("negatives") if p.strip()],
        )
        active_centroids = parse_centroids(request)
        active_weights = parse_weights(request, len(active_centroids))
        active_centroid = active_centroids[0] if active_centroids else None
        filename_pattern = parse_filename(request)
        # Resolve `allowed_ids` from the filename pattern up front so
        # any 400 lands before the long-running Qdrant search.
        allowed_ids, fname_err = await resolve_filename_filter(
            index_db, cfg=cfg, pattern=filename_pattern,
        )
        if fname_err == "bad_request":
            return bad_request(
                f"invalid filename pattern {filename_pattern!r}"
            )
        # Mutex: centroid search cannot coexist with text prompts.
        if active_centroids and (prompt_state.q or prompt_state.positives or prompt_state.negatives):
            return bad_request(
                "centroid search is exclusive — use ?centroid= or ?q=/?positives=, not both"
            )
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
            return JSONResponse(
                content=SearchResponse(
                    query="", positives=[], negatives=[],
                    diverse=diverse, surprise=surprise,
                    view=coerce_view(cfg.default_view),
                    centroid=active_centroid,
                    centroids=list(active_centroids),
                    weights=active_weights,
                    results=[], took_ms=0, offset=offset, limit=0, has_more=False,
                ).model_dump(),
                headers={"ETag": "deadbeef", "Cache-Control": "private, max-age=10"},
            )
        effective_limit = min(limit, cfg.max_results_total - offset)

        collections = parse_collections(request)

        if surprise and not prompt_state.positives and not active_centroids:
            # Surprise with no query: use zero vector so Qdrant
            # returns results from the whole collection.
            dim = cfg.centroid_expected_feature_dim
            vec = [0.0] * dim
        else:
            # Kick off vec resolution + (conditional) favorites fetch in
            # parallel — both touch different systems (SigLIP2 model vs
            # SQLite) so there's no contention. Saves ~30-100 ms on
            # queries where the user has favorites filtered.
            fav_task = (
                asyncio.create_task(favorite_ids_for_filter())
                if favorites else None
            )
            vec, vec_err, vec_detail = await asyncio.to_thread(
                resolve_query_vector,
                active_centroids, prompt_state, weights=active_weights,
                filename_pattern=filename_pattern,
                collections=collections,
            )
            if vec_err == "centroid_not_found":
                return bad_request(vec_detail or f"centroid {active_centroid!r} not loaded")  # type: ignore[return-value]
            if vec_err == "empty":
                return bad_request(vec_detail or "at least one positive prompt is required")  # type: ignore[return-value]

        t0 = time.time()
        # `allowed_ids == []` short-circuit: skip the Qdrant round-trip
        # and return zero hits. See the matching block in `/` for why
        # we can't pass `has_id=[]` directly to HasIdCondition.
        if allowed_ids is not None and not allowed_ids:
            hits: list = []
            has_more = False
            favorite_ids = set()
            diversity_meta = DiversityMetadata(
                requested=diverse,
                applied=False,
                mode=diversity_mode,
                strength=diversity_strength,
                depth=diversity_depth_mode,
                pool_depth=0,
            )
        else:
            try:
                if diverse:
                    favorite_ids = await fav_task if fav_task is not None else None
                    hits, has_more, diversity_meta = diversity_page(
                        cfg, qdrant, diversity_cache,
                        vector=vec,
                        effective_limit=effective_limit,
                        offset=offset,
                        collections=collections,
                        allowed_ids=allowed_ids,
                        favorite_ids=favorite_ids,
                        mode=diversity_mode,
                        strength=diversity_strength,
                        depth=diversity_depth_mode,
                        pool_depth=diversity_pool_depth,
                    )
                elif favorites:
                    favorite_ids = await fav_task
                    hits, has_more = await asyncio.to_thread(
                        qdrant.search,
                        vec,
                        effective_limit,
                        offset,
                        collections or None,
                        list(favorite_ids),
                    )
                    diversity_meta = DiversityMetadata()
                elif surprise:
                    # Pull a deep pool then random-sample. The pool
                    # depth is a multiple of `effective_limit` so the
                    # sample isn't degenerate on a small result set.
                    pool_size = max(effective_limit * 4, 200)
                    pool_hits, _ = await asyncio.to_thread(
                        qdrant.search,
                        vec,
                        pool_size,
                        offset,
                        collections or None,
                        allowed_ids,
                    )
                    hits = surprise_search(pool_hits, effective_limit)
                    has_more = len(pool_hits) >= pool_size
                    diversity_meta = DiversityMetadata()
                else:
                    favorite_ids = set()
                    hits, has_more = await asyncio.to_thread(
                        qdrant.search,
                        vec,
                        effective_limit,
                        offset,
                        collections or None,
                        allowed_ids,
                    )
                    diversity_meta = DiversityMetadata()
            except (ConnectionError, OSError) as e:
                logger.warning("Qdrant unreachable for /api/search: %s", e)
                return qdrant_unreachable(str(e))  # type: ignore[return-value]
            except Exception as e:
                if "timeout" in type(e).__name__.lower() or "Timeout" in str(e):
                    logger.warning("Qdrant timeout: %s", e)
                    return qdrant_timeout(str(e))  # type: ignore[return-value]
                logger.exception("search failed")
                return internal_error(str(e))  # type: ignore[return-value]
        took_ms = int((time.time() - t0) * 1000)
        # Resolve fav/dis for the SearchResult list via the indexed
        # helper. The diversity path already pulled the favorite set;
        # this is the parallel lookup against the IndexDB cache.
        results: list = await results_from_hits(
            index_db, cfg=cfg, hits=hits,
            favorite_ids=favorite_ids if favorites else None,
        )
        body = SearchResponse(
            query=prompt_state.q,
            positives=prompt_state.positives,
            negatives=prompt_state.negatives,
            diverse=diverse,
            surprise=surprise,
            diversity=diversity_meta,
            view=view,
            centroid=active_centroid,
            centroids=list(active_centroids),
            weights=active_weights,
            results=results,
            took_ms=took_ms,
            offset=offset,
            limit=limit,
            has_more=has_more,
        )
        # ETag over the canonical query-string so the browser's
        # If-None-Match on identical searches returns 304 (Tier 2.4).
        etag = hashlib.sha256(
            (q + "|" + ",".join(prompt_state.positives) + "|"
             + ",".join(prompt_state.negatives) + "|"
             + str(offset) + "|" + str(limit) + "|"
             + ",".join(active_centroids) + "|" + view).encode("utf-8")
        ).hexdigest()[:16]
        return JSONResponse(
            content=body.model_dump(),
            headers={
                "ETag": etag,
                "Cache-Control": "private, max-age=10",
            },
        )

    return router
