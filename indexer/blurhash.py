"""
indexer/blurhash.py — LQIP (low-quality image placeholder) hashing.

Blurhash encodes a small (e.g. 4×3) RGB thumbnail into a short string
(~20-40 chars) that the browser can decode into a colored placeholder.
We compute the hash at index time once and store it in the Qdrant
payload next to the path; the client side uses it for an instant
fill-in while the real image loads (T10 macro).

Why blurhash:
  - Tiny payload (~30 bytes vs. ~2KB for an embedded base64 thumb).
  - Decode is constant-time, no waterfall.
  - Brand colors survive compression at the cost of any fine detail
    (fine detail is exactly what we don't want in a placeholder).

The library is https://pypi.org/project/blurhash/ (pure-Python +
numpy, no native deps; reads what Pillow writes). Numpy is a
transitive dep of Pillow so it's always available in our venv.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Components for the tiny thumbnail. 4×3 = ~30 bytes for typical
# desktop content. Bigger components give a sharper placeholder at
# the cost of payload size; 4×3 strikes the visual/size balance.
_DEFAULT_X_COMPONENTS = 4
_DEFAULT_Y_COMPONENTS = 3

# Pillow tolerates most formats via `Image.open`; we resize before
# encoding to avoid feeding full-res images to the encoder (huge
# slowdown, no visual gain beyond the chosen component grid).
_THUMB_MAX_EDGE = 32


def compute_blurhash(
    source: "Path | Image.Image",
    x_components: int = _DEFAULT_X_COMPONENTS,
    y_components: int = _DEFAULT_Y_COMPONENTS,
) -> str | None:
    """
    Compute a Blurhash string for `source`. Returns None on any failure
    (missing/corrupt file, decode error, encode error) so callers can
    fall through to "no placeholder" without crashing the indexer.

    Args:
        source: source image on disk (`Path`) **or** an already-loaded
            PIL Image. Passing an image avoids an extra disk read +
            JPEG decode, which matters during bulk ingest.
        x_components: horizontal sample count (3-8 typical).
        y_components: vertical sample count (3-8 typical).
            Must be `<= x_components` (blurhash API rule).

    Returns:
        Blurhash string, or None if compute failed.
    """
    try:
        import blurhash as _blurhash
        import numpy as _np
        from PIL import Image
    except ImportError as exc:  # pragma: no cover — installs are pinned
        logger.debug("blurhash or Pillow not available: %s", exc)
        return None

    try:
        if isinstance(source, Image.Image):
            img = source
            owns_open = False
        else:
            img = Image.open(source)
            owns_open = True
        try:
            # Convert to RGB so we don't get a mode the encoder
            # doesn't understand (RGBA / P / L all map cleanly).
            rgb = img.convert("RGB")
            # Resize so the encoder runs in constant time regardless
            # of source size. `thumbnail` keeps aspect ratio.
            rgb.thumbnail((_THUMB_MAX_EDGE, _THUMB_MAX_EDGE), Image.Resampling.LANCZOS)
            # The blurhash API expects a 3-D array (h, w, 3) of
            # 0-255 sRGB integers — exactly what `np.array(<PIL RGB>)`
            # gives us. Flat `tobytes()` is wrong here; the encoder
            # treats it as a 1-D array and the component decode
            # misaligns.
            arr = _np.array(rgb, dtype=_np.uint8)
            encoded = _blurhash.encode(
                arr, x_components, y_components,
            )
            # Defense-in-depth: the upstream `blurhash` library is
            # stable, but a future encoder change, a numpy / Pillow
            # interaction edge case, or a corrupted input could
            # produce a string the client decoder can't read. Reject
            # malformed output here so the indexer stores `None`
            # instead of poison, and the client falls through to "no
            # placeholder" cleanly.
            if not is_valid_blurhash(encoded):
                logger.debug(
                    "blurhash: encoder returned invalid output for %s (len=%d)",
                    source, len(encoded) if isinstance(encoded, str) else 0,
                )
                return None
            return encoded
        finally:
            if owns_open:
                img.close()
    except FileNotFoundError:
        logger.debug("blurhash: file missing — %s", source)
        return None
    except (OSError, ValueError, TypeError) as exc:
        # PIL raises a long tail of exceptions for "we can't open this"
        # (UnidentifiedImageError, OSError, etc.). Treat them all the
        # same: "no placeholder for this one".
        logger.debug("blurhash: failed to encode %s: %s", path, exc)
        return None


def is_valid_blurhash(value) -> bool:
    """
    Lightweight guard for stored payload — empty / None / non-ascii
    strings are not a blurhash. Useful for tests + for skipping
    placeholder rendering on the client.

    The blurhash spec uses a base83 alphabet that includes `?` and
    `:` (among other punctuation), so a strict character whitelist
    is fragile across library versions. We use a structural check
    instead: a real blurhash is at least ~20 chars of printable
    ASCII, and never empty / None / bytes.
    """
    if not value or not isinstance(value, str):
        return False
    # The absolute minimum legal blurhash is 6 chars (size flag +
    # max value + DC + zero ACs, for a 1×1 component hash). Anything
    # shorter than that is structurally impossible and therefore
    # garbage. The upper bound is a sanity cap — real outputs from
    # the (4×3) default are 28 chars; even 8×8 (the most we ever
    # call the encoder with) is only 132 chars. 200 leaves room for
    # future bumps without letting truly absurd strings through.
    if len(value) < 6 or len(value) > 200:
        return False
    # Printable ASCII only (no newlines, no non-ASCII).
    return all(0x20 <= ord(c) <= 0x7E for c in value)
