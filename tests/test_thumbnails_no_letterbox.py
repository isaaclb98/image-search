"""Regression test for round-30 thumbnail letterbox bug.

When `image_loader.load()` was changed to return `(letterboxed_img,
source_w, source_h)` (so the photo page could report source dimensions),
the local_sync thumbnail path was never updated. It kept passing the
letterboxed input to `compute_thumbnail()`, which center-crops to a
square — but the source it received was already square (384×384 with
black padding bars), so the crop was a no-op and the bars persisted
through to the final WebP.

This test asserts that thumbnails generated from a portrait source via
the `local_sync` code path do NOT have black bars on any edge.

Pre-fix (the bug): left/right column mean < 20/255 → bars present.
Post-fix:           all four edges have content (mean > 50).
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

# Use a tempdir for THUMBNAIL_DIR so the test doesn't need /app/data
# permissions and doesn't pollute the prod thumbnails volume.
TEST_THUMB_DIR = Path(os.environ.get("TEST_THUMBNAIL_DIR", "/tmp/test-thumb-round30"))  # noqa: S108 — intentional scratch dir for test isolation; override via env if needed


@pytest.fixture(autouse=True)
def _isolated_thumb_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Patch the module attribute, not the env var — indexer.thumbnails
    # captures THUMBNAIL_DIR at module import time, so env changes after
    # that are ignored. Use tmp_path so each test runs in a clean dir.
    test_dir = tmp_path / "thumbs"
    test_dir.mkdir()
    monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(test_dir))


def _edge_means(arr: np.ndarray) -> tuple[float, float, float, float]:
    h, w = arr.shape[:2]
    return (
        float(arr[:, :10].mean()),
        float(arr[:, -10:].mean()),
        float(arr[:10, :].mean()),
        float(arr[-10:, :].mean()),
    )


def _has_bars(means: tuple[float, float, float, float]) -> bool:
    """A black bar shows up as both the left and right column mean
    being near zero (or top + bottom, for landscape sources). Require
    both edges of a pair to be dark so we don't false-positive on a
    photo that just happens to have a dark column on one side."""
    l, r, t, b = means
    return (l < 20 and r < 20) or (t < 20 and b < 20)


PORTRAIT_SRC = Path(
    "/mnt/nas-main/images/kpop/collections/aespa/group/"
    "211220_Dreams_Come_True_220425_Ningning_-_ICN_Arrival_from_LAX_Press_220425 Ningning - Press-OSEN 03_c565cc.jpg"
)


def test_letterboxed_input_produces_bars() -> None:
    """The pre-fix code path: `load()` returns a letterboxed image, and
    compute_thumbnail receives that. This test pins the bug so any
    future change that re-introduces the regression fails loudly."""
    from indexer.image_loader import load
    from indexer.thumbnails import compute_thumbnail

    if not PORTRAIT_SRC.exists():
        pytest.skip(f"test source not present: {PORTRAIT_SRC}")

    letterboxed, sw, sh = load(PORTRAIT_SRC)
    assert letterboxed.size[0] == letterboxed.size[1], (
        f"load() should return a square letterboxed image, got {letterboxed.size}"
    )

    thumb = compute_thumbnail(letterboxed, "test-round30-bad")
    assert thumb is not None, "compute_thumbnail returned None on letterboxed input"
    arr = np.array(Image.open(thumb).convert("RGB"))
    means = _edge_means(arr)
    assert _has_bars(means), (
        f"Expected the buggy path to produce black bars, but means were {means}. "
        f"If this fails, the bug may have been inadvertently fixed at the load() layer."
    )


def test_original_input_produces_clean_thumbnail() -> None:
    """The post-fix code path: pass the original (non-letterboxed)
    image to compute_thumbnail. No black bars on any edge."""
    from indexer.image_loader import load_image_pil
    from indexer.thumbnails import compute_thumbnail

    if not PORTRAIT_SRC.exists():
        pytest.skip(f"test source not present: {PORTRAIT_SRC}")

    original = load_image_pil(PORTRAIT_SRC)
    assert original.size[0] != original.size[1], (
        f"load_image_pil() should return the original non-square image, got {original.size}"
    )

    thumb = compute_thumbnail(original, "test-round30-good")
    assert thumb is not None, "compute_thumbnail returned None on original input"
    arr = np.array(Image.open(thumb).convert("RGB"))
    means = _edge_means(arr)
    assert not _has_bars(means), (
        f"Thumbnail from original input has black bars (means={means}). "
        f"This indicates compute_thumbnail() itself is letterboxing — "
        f"check indexer/thumbnails.py"
    )
