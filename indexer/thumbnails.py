"""
indexer/thumbnails.py — generate WebP thumbnails at index time.

Thumbnails are 256×256 max dimension, WebP q50. Storage layout:
  {THUMBNAIL_DIR}/{prefix}/{point_id}.webp

Two-level prefix (first 2 chars of point_id) avoids putting 2M files
in one directory. ~8K files per bucket at 2M scale.

Round-perf (issue #2): also writes three downscaled siblings (240,
360, 480) at index time so the frontend's srcset can pick the
smallest variant that fits the rendered tile. The 256px file is the
canonical fallback for anything we didn't anticipate. Sized files
live next to the canonical one and follow the same prefix layout:

  {THUMBNAIL_DIR}/{prefix}/{point_id}.w{240|360|480}.webp
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from PIL import Image

from indexer.upsert import id_for

THUMBNAIL_DIR = os.environ.get("THUMBNAIL_DIR", "/app/data/thumbnails")
THUMBNAIL_SIZE = (256, 256)
THUMBNAIL_QUALITY = 50
# Sized variants the frontend srcset advertises. Each is smaller than
# the canonical 256px so the browser picks the smallest variant that
# beats its rendered CSS pixels × device-pixel-ratio. Order doesn't
# matter; we just enumerate them.
THUMBNAIL_SIZES: tuple[int, ...] = (240, 180, 120)


logger = logging.getLogger(__name__)


def thumbnail_path(point_id: str) -> Path:
    """Compute deterministic path from point ID."""
    prefix = point_id[:2]
    return Path(THUMBNAIL_DIR) / prefix / f"{point_id}.webp"


def sized_thumbnail_path(point_id: str, width: int) -> Path:
    """Compute deterministic path for a sized variant.

    Matches the look-up `search/routers/thumbnails.py` does when the
    frontend asks for `?w={width}`. We always write the canonical
    256px file *and* these siblings so the endpoint's 404-fallback to
    the canonical file is a true safety net, not a routine path.
    """
    prefix = point_id[:2]
    return Path(THUMBNAIL_DIR) / prefix / f"{point_id}.w{width}.webp"


def compute_thumbnail(image: Image.Image, point_id: str) -> Path | None:
    """Generate 256px WebP thumbnail from already-decoded image.

    Args:
        image: PIL Image already opened by the pipeline (no extra decode)
        point_id: Qdrant point ID (sha1 hash)

    Returns:
        Path to the written thumbnail file, or None on failure
    """
    try:
        out_path = thumbnail_path(point_id)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Work on a copy to avoid mutating the pipeline's image.
        # Center-crop to a square (the shorter side), then resize to
        # THUMBNAIL_SIZE. The frontend displays thumbnails in 1:1 boxes
        # with object-fit: cover, so square-cropped images fill the box
        # rather than letterboxing with empty bands.
        thumb = image.copy()
        w, h = thumb.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        thumb = thumb.crop((left, top, left + side, top + side))
        thumb = thumb.resize(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
        thumb.save(out_path, "WEBP", quality=THUMBNAIL_QUALITY, method=4)

        # Round-perf (issue #2): write the downscaled siblings for the
        # frontend srcset. The 256-px canonical is always written
        # first; siblings are extra variants the browser may pick.
        #
        # All advertised sizes are smaller than the canonical edge
        # (we control THUMBNAIL_SIZES), so the loop below always runs
        # at least once. If a future maintainer adds a variant that's
        # >= the canonical, we'd be upscaling — which is worse than
        # serving the canonical file. The endpoint's 404-fallback to
        # the canonical handles that automatically; we skip the write
        # to avoid garbage files on disk.
        for size in THUMBNAIL_SIZES:
            if size >= THUMBNAIL_SIZE[0]:
                continue
            variant = thumb.resize((size, size), Image.Resampling.LANCZOS)
            variant_path = sized_thumbnail_path(point_id, size)
            variant.save(variant_path, "WEBP", quality=THUMBNAIL_QUALITY, method=4)

        return out_path
    except (OSError, ValueError, RuntimeError) as e:
        # Thumbnail decode/save can fail on corrupt images,
        # missing files, or PIL/encoder issues. The caller expects
        # None on any failure (no payload = no placeholder), but
        # narrow to realistic failure modes rather than catch-all.
        logger.warning("Failed to generate thumbnail for %s: %s", point_id, e)
        return None


def generate_thumbnail_for_path(image: Image.Image, path: Path, shard: str = "") -> Path | None:
    """Generate thumbnail for a given source path.

    Computes the point_id from the path (same as Qdrant upsert), then
    generates the thumbnail. This ensures the thumbnail path matches
    what the search side will look for.
    """
    point_id = id_for(path, shard)
    return compute_thumbnail(image, point_id)
