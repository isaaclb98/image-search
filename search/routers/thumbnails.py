"""
search/routers/thumbnails.py — thumbnail serving endpoint.

Serves pre-generated WebP thumbnails from THUMBNAIL_DIR.
Falls back to 404 if thumbnail doesn't exist (frontend uses blurhash).

Path pattern: {THUMBNAIL_DIR}/{prefix}/{point_id}.webp
  - prefix = first 2 chars of point_id (256 buckets)
  - ~8KB per image at 256px WebP q50
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from search.config import THUMBNAIL_DIR

logger = logging.getLogger(__name__)


def build_thumbnails_router() -> APIRouter:
    """Build the thumbnail serving router."""
    router = APIRouter()

    @router.get("/thumb/{point_id}")
    async def get_thumbnail(point_id: str) -> FileResponse:
        """
        Serve a pre-generated WebP thumbnail.

        Args:
            point_id: Qdrant point ID (32-char hex)

        Returns:
            WebP file with immutable cache headers

        Raises:
            404 if thumbnail doesn't exist (frontend falls back to blurhash)
        """
        # Validate point_id format (32-char hex or UUID with hyphens)
        clean_id = point_id.replace("-", "")
        if len(clean_id) != 32 or not all(c in "0123456789abcdef" for c in clean_id):
            raise HTTPException(status_code=400, detail="Invalid point_id format")

        # Compute thumbnail path using two-level prefix
        prefix = point_id[:2]
        thumb_path = Path(THUMBNAIL_DIR) / prefix / f"{point_id}.webp"

        if not thumb_path.exists():
            raise HTTPException(status_code=404, detail="Thumbnail not found")

        # Immutable cache: thumbnails are deterministic by point_id
        # and never change unless the image is reindexed (which changes the point_id)
        return FileResponse(
            path=thumb_path,
            media_type="image/webp",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "Access-Control-Allow-Origin": "*",
            },
        )

    return router
