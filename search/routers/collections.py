"""
search/routers/collections.py — /api/collections (§B2 step 6).

GET /api/collections:
    Return a list of distinct library (`collection` payload field)
    values with point counts. Drives the chip-style filter UI on
    the frontend; one call per page load.

The route hands the request straight to Qdrant. On connection
errors it returns a 502 with the documented `qdrant_unreachable`
error envelope so the client can retry vs. treat it as a stable
empty response.

Tests pin:
- The endpoint returns `{collections: [...]}` on a healthy Qdrant.
- A connection error surfaces as 502 with the documented envelope.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from search.models import ErrorResponse

logger = logging.getLogger(__name__)


def build_collections_router(*, qdrant: Any) -> APIRouter:
    """Build the collections router with the live Qdrant client."""
    router = APIRouter()

    @router.get("/api/collections")
    async def list_collections():
        """List distinct library collections with point counts."""
        try:
            return {"collections": qdrant.list_collections_with_counts()}
        except (ConnectionError, OSError) as e:
            logger.warning("Qdrant unreachable for /api/collections: %s", e)
            return JSONResponse(
                status_code=502,
                content=ErrorResponse(
                    error="qdrant_unreachable",
                    detail=str(e),
                    code="qdrant_unreachable",
                ).model_dump(),
            )

    return router
