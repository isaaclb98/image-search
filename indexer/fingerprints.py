"""Image fingerprints used by the search-side Diversity ranker.

The indexer stores two deliberately simple signals:

* ``content_sha256`` identifies byte-for-byte duplicate files.
* ``dhash`` is a compact perceptual fingerprint for resized grayscale
  structure.  It catches common copies, recompressions, and small edits
  without adding a native dependency or storing another image thumbnail.

Both values are payload metadata only; they are not Qdrant vector fields.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DHASH_SIZE = 8


def content_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str | None:
    """Return the SHA-256 digest of *path*, or ``None`` when unreadable."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(chunk_size), b""):
                digest.update(chunk)
    except (OSError, ValueError) as exc:
        logger.debug("fingerprint: sha256 failed for %s: %s", path, exc)
        return None
    return digest.hexdigest()


def dhash(
    source,
    hash_size: int = DEFAULT_DHASH_SIZE,
) -> str | None:
    """Return a difference hash for an image, or ``None`` when invalid.

    An 8x8 hash gives 64 structural bits while remaining tiny in a Qdrant
    payload. Hamming distance is used by the search ranker instead of exact
    equality so recompressed or lightly edited copies can be grouped.

    `source` may be either a `Path` (round‑19: original behaviour,
    re‑reads the file) **or** an already‑loaded PIL Image (skips the
    disk read + JPEG decode, which is the bulk‑ingest hot path).
    """
    if hash_size < 2:
        raise ValueError("hash_size must be >= 2")
    try:
        from PIL import Image, ImageOps

        image = source if isinstance(source, Image.Image) else Image.open(source)
        image = ImageOps.exif_transpose(image).convert("L")
        resampling = getattr(Image, "Resampling", Image)
        image = image.resize(
            (hash_size + 1, hash_size),
            resampling.LANCZOS,
        )
        pixels = list(image.getdata())
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("fingerprint: dhash failed for %s: %s", source, exc)
        return None

    bits = [
        pixels[row * (hash_size + 1) + col]
        > pixels[row * (hash_size + 1) + col + 1]
        for row in range(hash_size)
        for col in range(hash_size)
    ]
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:0{len(bits) // 4}x}"


def hamming_distance(left: str, right: str) -> int | None:
    """Return the bit distance between two equal-length hex hashes."""
    if not left or not right or len(left) != len(right):
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None


def compute_fingerprints(source) -> dict[str, str | None]:
    """Compute all Diversity payload fingerprints for *source*.

    `source` may be either a `Path` or an already-loaded PIL Image.
    Bulk ingest calls this with the in-memory letterboxed image to
    skip an extra disk read + JPEG decode.
    """
    return {
        "content_sha256": content_sha256(source) if not _is_pil_image(source) else None,
        "dhash": dhash(source),
    }


def _is_pil_image(x) -> bool:
    from PIL import Image as _Image
    return isinstance(x, _Image.Image)
