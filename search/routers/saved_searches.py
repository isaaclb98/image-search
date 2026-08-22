"""
search/routers/saved_searches.py — /api/saved-searches CRUD (§B2 step 2).

CRUD endpoints for saved searches (named text-query presets). Pure
IndexDB CRUD; no Qdrant or embedder calls. Depends on:

  - `index_db`: provides `create_saved_search`, `list_saved_searches`,
    `get_saved_search`, `delete_saved_search`. All sync; the router
    wraps them in `asyncio.to_thread` so the event loop stays free.
  - `bad_request`: helper returning a 400 JSON response.
  - pydantic models in `search.models`: `SavedSearch`,
    `SavedSearchCreateRequest`, `SavedSearchListResponse`,
    `ErrorResponse`.

The factory takes only what the endpoints need. Tests pin the
factory signature and the response shapes.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from search.models import (
    ErrorResponse,
    SavedSearch,
    SavedSearchCreateRequest,
    SavedSearchListResponse,
)


def build_saved_searches_router(
    *,
    index_db: Any,
) -> APIRouter:
    """Build the saved-searches router with the live dependencies."""
    router = APIRouter()

    @router.post(
        "/api/saved-searches",
        response_model=SavedSearch,
        status_code=201,
    )
    async def create_saved_search(body: SavedSearchCreateRequest) -> Any:
        # Name: trim, length-check. Empty / whitespace-only / >80
        # chars after strip → 400. The IndexDB also trims, but
        # validating here gives a precise error message and the
        # right status code.
        name = (body.name or "").strip()
        if not (1 <= len(name) <= 80):
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    error="bad_request",
                    detail="name must be 1\u201380 characters after trim",
                    code="bad_request",
                ).model_dump(),
            )
        # Prompts: strip and drop empty entries. At least one prompt
        # total across both lists must remain, otherwise the saved
        # search would be empty and useless.
        pos = [p.strip() for p in (body.positives or []) if isinstance(p, str) and p.strip()]
        neg = [p.strip() for p in (body.negatives or []) if isinstance(p, str) and p.strip()]
        if not pos and not neg:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    error="bad_request",
                    detail="at least one prompt is required in positives or negatives",
                    code="bad_request",
                ).model_dump(),
            )
        try:
            row = await asyncio.to_thread(
                index_db.create_saved_search, name, pos, neg,
            )
        except ValueError as e:
            # UNIQUE-name conflict comes through as ValueError from
            # IndexDB. Surface as 409 with code=conflict so the UI
            # can show "name already in use, pick another" without
            # guessing the cause.
            return JSONResponse(
                status_code=409,
                content=ErrorResponse(
                    error="conflict", detail=str(e), code="conflict",
                ).model_dump(),
            )
        return SavedSearch(**row)

    @router.get(
        "/api/saved-searches",
        response_model=SavedSearchListResponse,
    )
    async def list_saved_searches(
        limit: int = Query(
            200, description="max saved searches",
        ),
        offset: int = Query(
            0, description="offset into saved searches",
        ),
    ) -> SavedSearchListResponse:
        # Manual validation so we return 400 (not 422) for bad input.
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    error="bad_request", detail="limit must be an integer",
                    code="bad_request",
                ).model_dump(),
            )
        if not (1 <= limit <= 1000):
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    error="bad_request", detail="limit must be in [1, 1000]",
                    code="bad_request",
                ).model_dump(),
            )
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    error="bad_request", detail="offset must be an integer",
                    code="bad_request",
                ).model_dump(),
            )
        if offset < 0:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    error="bad_request", detail="offset must be >= 0",
                    code="bad_request",
                ).model_dump(),
            )
        # IndexDB read is sync — wrap to keep the event loop free.
        rows, total = await asyncio.to_thread(
            index_db.list_saved_searches, limit, offset,
        )
        return SavedSearchListResponse(
            saved_searches=[SavedSearch(**r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    @router.get(
        "/api/saved-searches/{saved_id}",
        response_model=SavedSearch,
    )
    async def get_saved_search(saved_id: int) -> SavedSearch:
        row = await asyncio.to_thread(index_db.get_saved_search, saved_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Saved search not found")
        return SavedSearch(**row)

    @router.delete("/api/saved-searches/{saved_id}", status_code=204)
    async def delete_saved_search(saved_id: int) -> None:
        ok = await asyncio.to_thread(index_db.delete_saved_search, saved_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Saved search not found")

    return router