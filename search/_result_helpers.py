"""
search/_result_helpers.py — small pure helpers shared across routers.

These were previously closure-bound to `app.create_app`, which
made `/api/search` and `/api/centroids/{name}/search`
unextractable. They're pure functions of their inputs (no
hidden state, no closure deps) so they live here.

What stays closure-bound (for now):
- `_results_from_hits`: needs `index_db` for fav/dis lookups
  + `_cfg.web_ui_url` for URL hydration. Will move here once
  the remaining closure helpers (`_favorite_id_set`,
  `_resolve_filename_filter`) are lifted.
- `_resolve_filename_filter`: needs `index_db` for
  `list_filename_match_ids`. Same blocker.
- `_favorite_id_set`: needs `index_db`. Same blocker.

Everything else is here.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from search.diversity import DiversityStats
from search.models import DiversityMetadata, ErrorResponse


def parse_filename(request: Request) -> str:
    """
    Read the optional `?filename=` query param, stripped.

    Single-value: the existing UI is a single text input. If the
    user ever sends multiple `?filename=` values, the first
    non-empty one wins (consistent with how single-valued
    `?centroid=` works in `parse_centroid`). Empty /
    whitespace-only returns "" so callers can do the standard
    "if not raw: skip" check.

    The pattern itself is validated later — `resolve_filename_filter`
    in `_indexed_helpers.py` translates the raw string to image
    ids via `IndexDB.path_token_ids` and surfaces 400 on
    invalid FTS5 syntax.
    """
    for raw in request.query_params.getlist("filename"):
        value = raw.strip()
        if value:
            return value
    return ""


def parse_collections(request: Request) -> list[str]:
    """
    Read all `?collection=` query params from the request, in
    stable order. The multi-value shape is what powers the
    chip-style filter UI on the frontend.

    `getlist()` preserves order and skips duplicates the way
    the URL is written; we don't dedupe here because the user
    might paste a duplicate and the search behavior is the
    same.
    """
    return [c for c in request.query_params.getlist("collection") if c]


def coerce_view(raw: str | None) -> str:
    """Map the `view` query param to a known value.

    Defaults to "grid" when the param is missing or
    unrecognised; the SPA currently renders only the grid but
    this lets a future feed view hook in without a contract
    change.
    """
    if raw in ("grid", "feed"):
        return raw
    return "grid"


def diversity_metadata(stats: DiversityStats) -> DiversityMetadata:
    """Convert a compute-side DiversityStats into the wire DiversityMetadata."""
    return DiversityMetadata(
        requested=stats.requested,
        applied=stats.applied,
        mode=stats.mode,
        strength=stats.strength,
        candidate_count=stats.candidate_count,
        result_count=stats.result_count,
        duplicate_images_collapsed=stats.duplicate_images_collapsed,
        semantic_groups_covered=stats.semantic_groups_covered,
        depth=stats.depth,
        pool_depth=stats.pool_depth,
    )


def bad_request(detail: str) -> JSONResponse:
    """Build a 400 JSONResponse with the documented error envelope."""
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(
            error="bad_request", detail=detail, code="bad_request",
        ).model_dump(),
    )


def internal_error(detail: str) -> JSONResponse:
    """Build a 500 JSONResponse with the documented error envelope."""
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="internal_error", detail=detail, code="internal_error",
        ).model_dump(),
    )


def qdrant_unreachable(detail: str) -> JSONResponse:
    """Build a 502 JSONResponse for Qdrant connectivity failures."""
    return JSONResponse(
        status_code=502,
        content=ErrorResponse(
            error="qdrant_unreachable", detail=detail, code="qdrant_unreachable",
        ).model_dump(),
    )


def qdrant_timeout(detail: str) -> JSONResponse:
    """Build a 504 JSONResponse for Qdrant timeout failures."""
    return JSONResponse(
        status_code=504,
        content=ErrorResponse(
            error="qdrant_timeout", detail=detail, code="qdrant_timeout",
        ).model_dump(),
    )
