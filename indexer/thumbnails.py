"""
indexer/thumbnails.py — generate WebP thumbnails at index time.

Thumbnails are 384×384 max dimension, WebP q80 4:4:4. Storage
layout: {THUMBNAIL_DIR}/{prefix}/{point_id}.webp

Two-level prefix (first 2 chars of point_id) avoids putting 2M files
in one directory. ~8K files per bucket at 2M scale.

Post the model-variant migration plan: the thumbnail is the same
resolution as the so400m model input (384×384). The embedder still
loads the original JPEG directly — see `image_loader.py`'s
`_resize_for_embedding` — but indexing now writes a single 384px
display asset. The canonical 256px file is no longer written; the
sized-variant tuple is empty.

Why a single 384px variant (vs. the pre-migration 240/180/120 trio):
the model's input resolution is 384, and 384px is enough to render
crisp up to 192 CSS pixels at 2× DPR — covers most desktop + tablet
tile sizes. The pre-migration 240 was already too small to be
sharp at 192 CSS px × 2× DPR; 480 would be overkill for the common
rendering path and waste storage. Single-variant keeps the on-disk
footprint manageable and lets the frontend use a single hardcoded
`?w=384` request without consulting a srcset.

Why 384 and not a new 240/360/480 trio: the storage win is real
(~22 GB → ~20 GB on a 902k corpus) and the rendering win is
neutral (the new 384px is sharp where the old 240 was blurry).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

from indexer.upsert import id_for

THUMBNAIL_DIR = os.environ.get("THUMBNAIL_DIR", "/app/data/thumbnails")
THUMBNAIL_SIZE = (384, 384)  # matches the so400m model input res
# Quality 80 — top of the Instagram / Google Photos / Apple Photos
# range. Preserves fine detail (faces, fabric, foliage) without the
# ~20% byte bloat of going to 85+. Below 70 starts eating high-
# frequency texture. Round-36 had this at 50 which kept broad color
# but crushed detail — every tile looked like a watercolor when
# viewed on a HiDPI display.
THUMBNAIL_QUALITY = 80
# Force 4:4:4 chroma subsampling on the WebP encode. PIL's
# default for quality<95 is 4:2:0, which subsamples the chroma
# channels and visibly mutes saturated colors at high-contrast
# edges (lips, fabric folds, neon signs). libvips/sharp
# (Immich, PhotoPrism) force 4:4:4 at quality>=80; we adopt the
# same threshold for parity with the libvips pipeline. Output
# bytes grow ~10-20% per file; visual gain is concentrated on
# already-saturated content (skin tones and low-saturation
# backgrounds are unaffected).
CHROMA_SUBSAMPLING = "4:4:4"
# Sized variants the frontend srcset advertises. Post-migration
# this is empty: a single 384px asset covers the common rendering
# sizes (up to 192 CSS px at 2× DPR = 384 px at the bitmap level)
# without the storage overhead of three variants. The frontend
# requests `?w=384` and the router serves the canonical 384px file.
THUMBNAIL_SIZES: tuple[int, ...] = ()


# Resize in linear-light sRGB, not gamma-encoded sRGB. PIL's
# LANCZOS resize averages pixels in the sRGB (gamma-encoded)
# colorspace, which is mathematically wrong: averaging two sRGB
# values that differ in brightness does NOT yield the correct
# mid-tone (the average of `pow(x, 2.4)` ≠ `pow(avg(x), 2.4)`).
# The libvips pipeline (Immich, PhotoPrism) resizes in linear
# light and converts back; we mirror that here.
#
# The visual difference on K-pop photos is small (<0.3% on
# saturation/value/contrast vs. gamma-encoded resize) — see
# the measurement in references/thumbnail-color-pipeline.md
# — but the principle is right and the cost is ~3× the
# resize time for the extra gamma round-trip. Linear resize
# only runs at index time, not on serving.
_LINEAR_RESIZE = True


# sRGB ICC profile bytes. PIL strips ICC profiles from PIL
# Images on crop/resize by default; without an explicit
# `icc_profile=` argument to `save()` the output WebP is
# untagged. Browsers default-interpret untagged WebP as sRGB,
# which is correct for Telegram JPEGs (no profile on input),
# but a tagged sRGB profile makes the intent explicit and
# rounds-trips cleanly through any tool that respects ICC
# (color-managed browsers, image editors, PhotoPrism/Immich
# side-by-side comparisons). The 588 bytes is the standard
# sRGB profile from ImageCms; cached at module load so the
# per-thumbnail encode doesn't rebuild it.
_SRGB_PROFILE_BYTES: bytes = ImageCms.ImageCmsProfile(
    ImageCms.createProfile("sRGB")
).tobytes()


def _srgb_to_linear(channel: np.ndarray) -> np.ndarray:
    """Convert sRGB-encoded channel to linear-light, per IEC 61966-2-1.

    The sRGB curve has a linear segment near black (channel <= 0.04045)
    and a power-2.4 segment above. Inverting it is what linear-light
    resampling needs to operate on physically-meaningful values.
    """
    return np.where(
        channel <= 0.04045,
        channel / 12.92,
        ((channel + 0.055) / 1.055) ** 2.4,
    )


def _linear_to_srgb(channel: np.ndarray) -> np.ndarray:
    """Convert linear-light channel back to sRGB-encoded.

    Inverse of `_srgb_to_linear`. The output range is [0, 1] in
    float; round/clip to uint8 before re-assembling the image.
    """
    return np.where(
        channel <= 0.0031308,
        channel * 12.92,
        1.055 * (np.maximum(channel, 0.0) ** (1.0 / 2.4)) - 0.055,
    )


def _resize_in_linear_light(
    thumb: Image.Image, size: tuple[int, int]
) -> Image.Image:
    """Resize `thumb` to `size` in linear-light sRGB, then convert back.

    PIL's RGB resize averages sRGB-encoded pixel values, which is the
    mathematically-wrong place to interpolate. This helper does the
    full round-trip:
      1. uint8 RGB  →  float32 linear-light  (per channel)
      2. LANCZOS resize each channel independently
      3. linear-light  →  sRGB-encoded
      4. uint8 RGB

    Channels are split during the resize because PIL's LANCZOS works
    on 2D planes, not 3-channel tensors. Splitting is 3× the
    resize() calls but each is a tiny per-channel op; the total
    cost is ~3-5ms per thumbnail on a typical CPU.

    Input is expected to be RGB (the canonical thumb path calls
    `.convert("RGB")` before this). Output is RGB.
    """
    arr = np.asarray(thumb, dtype=np.float32) / 255.0
    out_w, out_h = size

    # Per-channel linear-light resize.
    resized_channels: list[np.ndarray] = []
    for c in range(3):
        linear = _srgb_to_linear(arr[:, :, c])
        # 16-bit container for the LANCZOS pass; PIL's LANCZOS on
        # float32 isn't supported across all Pillow versions and
        # the precision loss at 8-bit is visible on the dark
        # shadows that linearization reveals.
        lin16 = Image.fromarray(
            np.clip(linear * 65535.0, 0, 65535).astype(np.uint16),
            mode="I;16",
        )
        resized = lin16.resize((out_w, out_h), Image.Resampling.LANCZOS)
        resized_channels.append(
            np.asarray(resized, dtype=np.float32) / 65535.0
        )

    # Stack, convert back to sRGB, clip, return as RGB uint8.
    linear_stack = np.stack(resized_channels, axis=-1)
    srgb_stack = _linear_to_srgb(linear_stack)
    srgb_u8 = np.clip(np.round(srgb_stack * 255.0), 0, 255).astype(np.uint8)
    return Image.fromarray(srgb_u8, mode="RGB")


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
        # Resize in linear-light sRGB (see _resize_in_linear_light
        # for why). The crop stays in sRGB — gamma correction
        # is only meaningful at the resampling step, where
        # neighboring pixels are weighted-averaged.
        if _LINEAR_RESIZE:
            thumb_rgb = thumb.convert("RGB")
            thumb = _resize_in_linear_light(thumb_rgb, THUMBNAIL_SIZE)
        else:
            thumb = thumb.resize(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
        # Force 4:4:4 chroma subsampling on the WebP encode. PIL's
        # default for quality<95 is 4:2:0, which subsamples the
        # chroma channels and visibly mutes saturated colors at
        # high-contrast edges (lips, fabric folds, neon signs).
        # libvips/sharp (Immich, PhotoPrism) force 4:4:4 at
        # quality>=80; we adopt the same threshold for parity with
        # the libvips pipeline. Output bytes grow ~10-20% per
        # file; visual gain is concentrated on already-saturated
        # content (skin tones and low-saturation backgrounds are
        # unaffected). Embed the sRGB ICC profile so the WebP is
        # explicitly color-managed (browsers with strict color
        # management render tagged sRGB slightly more accurately
        # than untagged; image editors round-trip cleanly).
        thumb.save(
            out_path,
            "WEBP",
            quality=THUMBNAIL_QUALITY,
            method=4,
            chroma_subsampling=CHROMA_SUBSAMPLING,
            icc_profile=_SRGB_PROFILE_BYTES,
        )

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
            # Same linear-light resize as the canonical thumb so
            # the srcset doesn't have a per-size color shift.
            if _LINEAR_RESIZE:
                variant = _resize_in_linear_light(thumb, (size, size))
            else:
                variant = thumb.resize((size, size), Image.Resampling.LANCZOS)
            variant_path = sized_thumbnail_path(point_id, size)
            # Same 4:4:4 chroma + sRGB profile as the canonical;
            # sized variants are an edge case (THUMBNAIL_SIZES is
            # currently empty) but the encode should match the
            # canonical for consistent color rendering across
            # the srcset.
            variant.save(
                variant_path,
                "WEBP",
                quality=THUMBNAIL_QUALITY,
                method=4,
                chroma_subsampling=CHROMA_SUBSAMPLING,
                icc_profile=_SRGB_PROFILE_BYTES,
            )

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
