"""
tests/conftest.py

Shared pytest fixtures:
  - test mode env vars (SEARCH_TEST_MODE=1, NAS_IMAGES_BASE=tmp_path)
  - in-memory Qdrant client wired into a search.app
  - tiny test PNG fixtures generated on the fly
  - reset text encoder singleton between tests
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure repo root is on sys.path so `import search` works without
# the package being installed. Tests should work without `pip install -e .`
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Set env vars BEFORE any search.* import so config.load() sees them.
os.environ.setdefault("SEARCH_TEST_MODE", "1")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")  # unused; we pass :memory:
os.environ.setdefault("MODEL_NAME", "ViT-gopt-16-SigLIP2-384")
os.environ.setdefault("TOP_K_DEFAULT", "35")
os.environ.setdefault("TOP_K_MAX", "200")


@pytest.fixture
def qdrant_in_memory():
    """A QdrantClient in :memory: mode, function-scoped (fresh per test)."""
    from qdrant_client import QdrantClient

    from search.qdrant_client import QdrantSearch

    client = QdrantClient(location=":memory:")
    yield QdrantSearch(client=client, collection="images_test", timeout_ms=2000)


@pytest.fixture
def fixture_images(tmp_path: Path) -> Path:
    """
    Create a small set of tiny PNG images for tests.
    Returns the directory path.
    """
    from PIL import Image

    images_dir = tmp_path / "images"
    images_dir.mkdir()
    # 5 distinct images with different sizes so id_for produces distinct ids.
    for i, (size, color) in enumerate(
        [
            (16, (255, 0, 0)),
            (24, (0, 255, 0)),
            (32, (0, 0, 255)),
            (40, (255, 255, 0)),
            (48, (255, 0, 255)),
        ]
    ):
        img = Image.new("RGB", (size, size), color)
        img.save(images_dir / f"img_{i:02d}.png")
    # A hidden file that should be skipped.
    (images_dir / ".hidden.jpg").write_bytes(b"not an image")
    # A Thumbs.db that should be skipped.
    (images_dir / "Thumbs.db").write_bytes(b"junk")
    # A non-image file.
    (images_dir / "notes.txt").write_text("hello")
    return images_dir


@pytest.fixture
def nas_base(tmp_path: Path) -> Path:
    """Empty NAS base for resolve_local tests."""
    p = tmp_path / "nas"
    p.mkdir()
    return p


@pytest.fixture(autouse=True)
def _set_nas_base_env(nas_base: Path, monkeypatch):
    """Wire NAS_IMAGES_BASE so config.load() succeeds."""
    monkeypatch.setenv("NAS_IMAGES_BASE", str(nas_base))


# Re-export the shared centroid test fixture so pytest discovers it.
# The fixture + constants + helper live in `_centroid_fixture.py`
# (a regular module that's also importable from test files). See
# that file's docstring for why we don't put them directly here.
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
from _centroid_fixture import app_with_centroids  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_text_encoder():
    """Drop the encoder singleton between tests so test_mode is respected."""
    from search import text_encoder

    text_encoder.reset_encoder_for_tests()
    text_encoder.clear_cache()
    yield
    text_encoder.reset_encoder_for_tests()
    text_encoder.clear_cache()
