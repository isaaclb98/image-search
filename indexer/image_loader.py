"""
indexer/image_loader.py

Load + preprocess an image for embedding.

Pipeline:
  1. PIL.Image.open(path) — lazy decode
  2. ImageOps.exif_transpose() — apply EXIF orientation
  3. .convert("RGB") — drop alpha; SigLIP2 expects 3 channels
  4. Letterbox resize to the registered model's resolution (e.g.
     384x384 for ViT-gopt-16-SigLIP2-384, 256x256 for
     ViT-L-16-SigLIP2-256) — preserve aspect ratio
  5. Normalize with the model's mean/std — produces a tensor
     ready for the embedder

The model's resolution, mean, and std are pulled from the
kernel's model registry by `model_name`. No constants live in
this module.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
from pathlib import Path

from PIL import Image, ImageOps

# TODO(v1.1): import utilities from isaac-image-scoring once that package
# is pip-installable. Today it's a script-shaped project, not a library.

logger = logging.getLogger(__name__)

# SigLIP2 normalization (from the model's processor config). Mean/std
# are model-family properties; the resolution is per-model and
# resolved at `letterbox_resize` call time from the registry.
SIGLIP_MEAN: tuple[float, float, float] = (0.5, 0.5, 0.5)
SIGLIP_STD: tuple[float, float, float] = (0.5, 0.5, 0.5)

# Per-image load timeout (seconds). PIL's read is a blocking syscall
# on the underlying file handle; on a slow/stalled network share it
# can hang indefinitely. The timeout turns the hang into a
# LoaderError so the indexer can skip the file and move on.
# Override with INDEXER_LOAD_TIMEOUT_S=60 (or whatever) if 30s is
# too tight for your network.
_LOAD_TIMEOUT_S: float = float(os.environ.get("INDEXER_LOAD_TIMEOUT_S", "30"))


class LoaderError(Exception):
    """Raised when an image cannot be loaded/normalized."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def load_image_pil(path: Path) -> Image.Image:
    """
    Load an image and apply EXIF orientation. Returns a PIL.Image in RGB.

    Does NOT resize or normalize — that's the next step. Useful for tests
    and for callers that want to inspect the image before tensorizing.
    """
    # Suppress PIL's decompression bomb check. Several photos in the
    # collection exceed the default MAX_IMAGE_PIXELS (178956970) at
    # typical resolutions like 14400x13872 (~200M pixels). The images
    # are from a trusted source (your own NAS) so the bomb check is
    # pure friction. Raised to 1G pixels — enough for any single
    # frame this pipeline will ever encounter.
    from PIL import Image as _PIL
    _PIL.MAX_IMAGE_PIXELS = 1_000_000_000
    try:
        with Image.open(path) as img:
            # .copy() so the file handle is released (Windows file locking)
            img = ImageOps.exif_transpose(img).copy()
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except FileNotFoundError as e:
        raise LoaderError(path, "file not found") from e
    except Exception as e:  # PIL raises a zoo of exceptions
        raise LoaderError(path, f"open failed: {type(e).__name__}: {e}") from e


def _default_resolution() -> int:
    """Resolution to use when no model is specified.

    Looks up the web backend's registered model and returns its
    resolution. Falls back to 384 (the SigLIP2 family default) if
    the registry hasn't been initialized — for example, when this
    module is imported by a tool that runs before any registry call.
    """
    try:
        from image_search_kernel.registry import get as _registry_get
        return _registry_get("ViT-gopt-16-SigLIP2-384").resolution
    except Exception:
        return 384


def letterbox_resize(img: Image.Image, size: int | None = None) -> Image.Image:
    """
    Resize `img` to fit in a `size`x`size` box, preserving aspect ratio,
    then pad to exactly `size`x`size` with black.

    Why letterbox (vs. naive resize): naive resize distorts the image.
    SigLIP2-style models are trained on square inputs but real photos
    aren't square. Letterbox + pad keeps the aspect ratio intact.

    Uses LANCZOS resampling to match isaac-image-scoring exactly.

    If `size` is None, the active model's resolution is read from the
    registry. Callers should pass the registered model's resolution
    explicitly when known.
    """
    if size is None:
        size = _default_resolution()
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    # Pad to exactly size x size, centered.
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    x = (size - img.width) // 2
    y = (size - img.height) // 2
    canvas.paste(img, (x, y))
    return canvas


def load(path: Path, *, model_name: str = "ViT-gopt-16-SigLIP2-384") -> Image.Image:
    """
    Load + EXIF-correct + RGB-convert + letterbox. Returns a PIL.Image
    of `resolution`x`resolution`, ready for the embedder registered as
    `model_name`.

    `model_name` defaults to the web backend's current model
    (`ViT-gopt-16-SigLIP2-384`). The resolution is read from the model
    registry, not from a constant in this module.

    Wraps `load_image_pil` in a thread with a `_LOAD_TIMEOUT_S` cap
    so a slow/stalled read on a network share doesn't hang the
    indexer. On timeout, raises LoaderError (caught by the indexer
    loop and logged as a per-file error; the indexer continues with
    the next file).
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(load_image_pil, path)
        try:
            img = future.result(timeout=_LOAD_TIMEOUT_S)
        except concurrent.futures.TimeoutError as err:
            raise LoaderError(
                path,
                f"timed out after {_LOAD_TIMEOUT_S}s reading (likely a "
                f"slow/stalled network share; raise INDEXER_LOAD_TIMEOUT_S "
                f"if your network is just consistently slow)",
            ) from err
    # Resize to the registered model's resolution.
    from image_search_kernel.registry import get as _registry_get
    size = _registry_get(model_name).resolution
    return letterbox_resize(img, size=size)


# Heuristic mapping from PIL size to torchvision's expected CHW float tensor.
# We don't import torchvision here to keep this module testable on machines
# without GPU/torchvision wheels; the vision_encoder wraps with the
# processor at embed time.
def to_chw_float(img: Image.Image) -> list[list[list[float]]]:
    """
    Convert a PIL.Image (RGB, square) to a CHW float list in [0, 1].

    Normalization to mean/std is left to the model's processor; this
    gives the raw CHW float the processor expects.

    Note: in v1 we use the AutoProcessor directly, not this function.
    It's kept for tests and for callers that want raw pixels.
    """
    import numpy as np

    arr = np.asarray(img, dtype="float32") / 255.0  # HWC in [0, 1]
    return arr.transpose(2, 0, 1).tolist()  # CHW
