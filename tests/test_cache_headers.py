"""
tests/test_cache_headers.py — Cache-Control headers on static + photo endpoints.

§C5 of `docs/backend-refactor-plan.md`:
  - `GET /_app/immutable/*` must return
    `Cache-Control: max-age=31536000, immutable` (1-year cache).
  - `GET /photo/{id}/raw` must return `Cache-Control: must-revalidate`
    so re-validation happens on photo reloads (a new file at the
    same path is a real event, not a stale cache).

Both assertions fail CI on regression. Browsers' default caching
behavior changes when these headers are missing; the test catches
the regression before users do.
"""

from __future__ import annotations

import pytest
from PIL import Image

# ---------------------------------------------------------------------------
# /_app/* — content-hashed SvelteKit assets, 1-year immutable
# ---------------------------------------------------------------------------

@pytest.fixture
def static_assets_dir(tmp_path):
    """Build a minimal `app_static/_app/immutable/...` tree."""
    assets = tmp_path / "_app" / "immutable" / "assets"
    assets.mkdir(parents=True)
    (assets / "logo.png").write_bytes(b"fake-png-bytes")
    (tmp_path / "_app" / "immutable" / "entry").mkdir(parents=True)
    (tmp_path / "_app" / "immutable" / "entry" / "start.js").write_bytes(b"// js")
    return tmp_path


def test_app_immutable_assets_carry_immutable_cache_header(static_assets_dir, monkeypatch):
    """`GET /_app/immutable/*` sets `Cache-Control: ...immutable`."""

    from fastapi.testclient import TestClient
    from qdrant_client import QdrantClient

    from search.app import create_app, reset_for_tests
    from search.qdrant_client import QdrantSearch

    # The static dir is read from the FRONTEND_DIR env var in create_app.
    monkeypatch.setenv("FRONTEND_DIR", str(static_assets_dir))

    reset_for_tests()
    client = QdrantClient(location=":memory:")
    qdrant = QdrantSearch(client=client, collection="cache_test", timeout_ms=2000)
    app = create_app(qdrant=qdrant)
    with TestClient(app) as http:
        resp = http.get("/_app/immutable/assets/logo.png")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "immutable" in cc.lower(), (
            f"missing 'immutable' in Cache-Control: {cc!r}"
        )
        # 1-year cache (31536000 seconds).
        assert "max-age=31536000" in cc, (
            f"missing 'max-age=31536000' in Cache-Control: {cc!r}"
        )


def test_app_immutable_js_carry_immutable_cache_header(static_assets_dir, monkeypatch):
    """`/_app/immutable/entry/start.js` also gets immutable (per plan §C5)."""

    from fastapi.testclient import TestClient
    from qdrant_client import QdrantClient

    from search.app import create_app, reset_for_tests
    from search.qdrant_client import QdrantSearch

    monkeypatch.setenv("FRONTEND_DIR", str(static_assets_dir))
    reset_for_tests()
    client = QdrantClient(location=":memory:")
    qdrant = QdrantSearch(client=client, collection="cache_test", timeout_ms=2000)
    app = create_app(qdrant=qdrant)
    with TestClient(app) as http:
        resp = http.get("/_app/immutable/entry/start.js")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "immutable" in cc.lower()


# ---------------------------------------------------------------------------
# /photo/{id}/raw — photo bytes, must-revalidate
# ---------------------------------------------------------------------------

@pytest.fixture
def photo_in_corpus(tmp_path):
    """Create one JPEG under tmp_path and return (corpus_dir, photo_path)."""
    corpus = tmp_path / "photos"
    corpus.mkdir()
    p = corpus / "sample.jpg"
    Image.new("RGB", (32, 32), color=(10, 20, 30)).save(p, "JPEG")
    return corpus, p


def test_photo_raw_response_has_must_revalidate_cache_control(
    photo_in_corpus, monkeypatch,
):
    """`GET /photo/{id}/raw` returns `Cache-Control: must-revalidate`.

    The plan calls for `must-revalidate` so the browser revalidates
    on every reload — photos can change (file replaced on disk,
    re-indexed), and a stale cache would show the wrong image.
    """

    from fastapi.testclient import TestClient
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels

    from indexer.upsert import build_payload, ensure_collection, id_for, upsert_batch
    from search.app import create_app, reset_for_tests
    from search.config import Config
    from search.config import load as load_config
    from search.qdrant_client import QdrantSearch

    corpus, photo_path = photo_in_corpus
    reset_for_tests()

    client = QdrantClient(location=":memory:")
    qdrant = QdrantSearch(client=client, collection="photo_cache_test", timeout_ms=2000)

    # Build a Config that points at our tmp_path corpus.
    base = load_config()
    cfg = Config(
        **{**base.__dict__, "nas_images_base": str(corpus.parent)}
    )

    ensure_collection(client, "photo_cache_test", dim=1536)
    pid = id_for(photo_path)
    pt = qmodels.PointStruct(
        id=pid,
        vector=[0.0] * 1536,
        payload=build_payload(
            photo_path, shard="",
            model_name="test", model_revision="r0",
            collection="photo_cache_test",
            model_dim=1536,
        ),
    )
    upsert_batch(client, "photo_cache_test", [(pid, pt.vector, pt.payload)], wait=True)

    app = create_app(cfg=cfg, qdrant=qdrant)
    with TestClient(app) as http:
        resp = http.get(f"/photo/{pid}/raw")
        assert resp.status_code == 200, (
            f"GET /photo/{pid}/raw failed: {resp.text}"
        )
        cc = resp.headers.get("cache-control", "")
        assert "must-revalidate" in cc.lower(), (
            f"missing 'must-revalidate' in Cache-Control: {cc!r}"
        )
