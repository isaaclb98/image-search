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


def content_sha256(
    source: Path | bytes | bytearray,
    chunk_size: int = 1024 * 1024,
) -> str | None:
    """Return the SHA-256 digest of *source*, or ``None`` when unreadable.

    `source` may be either a `Path` (reads the file) **or** an in-memory
    `bytes` object (hashes them directly — the bulk‑ingest hot path uses
    this to avoid a second disk read after the JPEG decode already loaded
    the bytes for `dhash`).
    """
    digest = hashlib.sha256()
    try:
        if isinstance(source, (bytes, bytearray)):
            digest.update(source)
        else:
            with source.open("rb") as handle:
                for chunk in iter(lambda: handle.read(chunk_size), b""):
                    digest.update(chunk)
    except (OSError, ValueError) as exc:
        logger.debug("fingerprint: sha256 failed for %s: %s", source, exc)
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


def compute_fingerprints(
    source,
    *,
    sha_bytes: bytes | None = None,
) -> dict[str, str | None]:
    """Compute all Diversity payload fingerprints for *source*.

    `source` may be either a `Path` or an already-loaded PIL Image
    (used to skip the JPEG decode for `dhash`). For the content
    sha256, pass `sha_bytes=path.read_bytes()` once at the call site
    and the byte hash is amortized into the same disk read that PIL
    already performs during decode.
    """
    if sha_bytes is not None:
        sha_input: Path | bytes = sha_bytes
    elif _is_pil_image(source):
        sha_input = source  # falls back to hashing source (won't match file bytes; only used by legacy callers)
    else:
        sha_input = source
    return {
        "content_sha256": content_sha256(sha_input),
        "dhash": dhash(source),
    }


def _is_pil_image(x) -> bool:
    from PIL import Image as _Image
    return isinstance(x, _Image.Image)
