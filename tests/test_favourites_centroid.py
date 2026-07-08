"""Tests for the favourites dynamic centroid + DynamicCentroidRegistry.

Two layers:
- Unit tests for the registry (compute, invalidate, cache).
- End-to-end tests through the FastAPI app (mark favourite,
  /api/centroids list, /api/centroids/favourites/search).
"""
from __future__ import annotations

import math
import pytest

from indexer import upsert
from indexer.upsert import VECTOR_DIM
from search.centroids import (
    DynamicCentroidRegistry,
    DynamicCentroidSpec,
)
from search.text_encoder import _mock_embed


def _vec(seed: str) -> list[float]:
    """Return a deterministic unit-length vector for use in tests."""
    e = _mock_embed(seed)
    norm = math.sqrt(sum(x * x for x in e))
    return [x / norm for x in e]


def test_registry_register_and_get_vector():
    reg = DynamicCentroidRegistry()
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return ([1.0, 0.0, 0.0], 5)

    reg.register(DynamicCentroidSpec(
        name="t", label="t", description="", source="x", compute_fn=compute,
    ))
    assert reg.names() == ["t"]
    assert reg.cached_n_images("t") is None
    assert reg.is_empty("t") is False

    vec, n = reg.get_vector("t")
    assert vec == [1.0, 0.0, 0.0]
    assert n == 5
    assert calls["n"] == 1
    assert reg.cached_n_images("t") == 5
    assert reg.is_empty("t") is False

    vec2, n2 = reg.get_vector("t")
    assert vec2 == vec
    assert n2 == 5
    assert calls["n"] == 1


def test_registry_invalidate_triggers_recompute():
    reg = DynamicCentroidRegistry()
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return ([float(calls["n"])], 1)

    reg.register(DynamicCentroidSpec(
        name="t", label="t", description="", source="x", compute_fn=compute,
    ))
    assert reg.get_vector("t")[0] == [1.0]
    reg.invalidate("t")
    assert reg.get_vector("t")[0] == [2.0]
    assert calls["n"] == 2


def test_registry_invalidate_unknown_name_is_noop():
    reg = DynamicCentroidRegistry()
    reg.invalidate("not-registered")


def test_registry_unregister_removes_spec():
    """unregister drops the spec from _by_name so list() stops returning it."""
    reg = DynamicCentroidRegistry()
    reg.register(DynamicCentroidSpec(
        name="t", label="t", description="", source="x",
        compute_fn=lambda: ([1.0, 0.0], 3),
    ))
    assert reg.names() == ["t"]
    # Prime the cache so we can verify it gets dropped too.
    assert reg.get_vector("t") == ([1.0, 0.0], 3)
    assert reg.cached_n_images("t") == 3

    reg.unregister("t")

    assert reg.names() == []
    assert reg.get_vector("t") is None
    assert reg.cached_n_images("t") is None
    # And the spec lookup is gone (used by /api/centroids to
    # render the row).
    assert reg.get_spec("t") is None


def test_registry_unregister_unknown_name_is_noop():
    """unregister on an unregistered name doesn't raise. Lets the
    DELETE-album path call it unconditionally."""
    reg = DynamicCentroidRegistry()
    reg.unregister("never-registered")
    assert reg.names() == []


def test_registry_unregister_clears_dirty_flag():
    """After invalidate, the name is in _dirty; unregister must
    clear that too so a subsequent register doesn't see a stale
    dirty marker."""
    reg = DynamicCentroidRegistry()
    reg.register(DynamicCentroidSpec(
        name="t", label="t", description="", source="x",
        compute_fn=lambda: ([1.0], 1),
    ))
    reg.invalidate("t")
    reg.unregister("t")
    # Re-register — next get_vector should compute exactly once.
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return ([float(calls["n"])], 1)

    reg.register(DynamicCentroidSpec(
        name="t", label="t", description="", source="x", compute_fn=compute,
    ))
    assert reg.get_vector("t")[0] == [1.0]
    assert calls["n"] == 1


def test_registry_compute_returns_none_is_empty():
    reg = DynamicCentroidRegistry()
    reg.register(DynamicCentroidSpec(
        name="t", label="t", description="", source="x",
        compute_fn=lambda: None,
    ))
    assert reg.get_vector("t") is None
    assert reg.is_empty("t") is True
    assert reg.cached_n_images("t") is None


def test_registry_compute_exception_is_swallowed():
    reg = DynamicCentroidRegistry()
    state = {"should_raise": True}

    def compute():
        if state["should_raise"]:
            raise RuntimeError("boom")
        return ([1.0], 1)

    reg.register(DynamicCentroidSpec(
        name="t", label="t", description="", source="x", compute_fn=compute,
    ))
    assert reg.get_vector("t") is None

    state["should_raise"] = False
    reg.invalidate("t")
    vec, n = reg.get_vector("t")
    assert vec == [1.0]
    assert n == 1


def test_registry_get_vector_unknown_name_returns_none():
    reg = DynamicCentroidRegistry()
    assert reg.get_vector("nope") is None


def test_registry_list_returns_sorted():
    reg = DynamicCentroidRegistry()
    for name in ["b", "a", "c"]:
        reg.register(DynamicCentroidSpec(
            name=name, label=name, description="", source="x",
            compute_fn=lambda: ([1.0], 1),
        ))
    assert [s.name for s in reg.list()] == ["a", "b", "c"]


@pytest.fixture
def fav_app(tmp_path):
    import uuid
    from fastapi.testclient import TestClient
    from qdrant_client import QdrantClient
    from search.app import create_app
    from search.config import Config
    from search.qdrant_client import QdrantSearch

    cfg = Config(
        qdrant_url="memory://",
        qdrant_collection="images_test_fav_centroid",
        qdrant_api_key=None,
        model_name="mock",
        model_revision="",
        device="cpu",
        top_k_default=50,
        top_k_max=200,
        query_timeout_ms=2000,
        nas_images_base=str(tmp_path),
        path_prefix="",
        web_ui_url="http://localhost:8000",
        log_level="WARNING",
        index_db_path=str(tmp_path / "images.db"),
        test_mode=True,
    )

    client = QdrantClient(location=":memory:")
    upsert.ensure_collection(client, cfg.qdrant_collection, dim=VECTOR_DIM)
    seed_ids = {
        "a": str(uuid.uuid4()),
        "b": str(uuid.uuid4()),
        "c": str(uuid.uuid4()),
    }
    items = [
        (seed_ids[k], _mock_embed(k),
         {"id": seed_ids[k], "path": f"/photos/{k}.jpg",
          "collection": "kpop", "indexed_at": "2026-01-01T00:00:00Z"})
        for k in seed_ids
    ]
    upsert.upsert_batch(client, cfg.qdrant_collection, items, wait=True)

    qdrant = QdrantSearch(
        client=client, collection=cfg.qdrant_collection, timeout_ms=2000,
    )

    app = create_app(cfg=cfg, qdrant=qdrant)
    app.state.test_seed_ids = seed_ids
    app.state.qdrant_client = client
    app.state.qdrant_collection = cfg.qdrant_collection
    # Eagerly init the cache from Qdrant so tests don't race with
    # the lifespan handler. TestClient runs lifespan asynchronously;
    # the init_from_qdrant call may not have finished by the time
    # the first test request arrives.
    import search.app as _app_mod
    _app_mod._index_db.init_from_qdrant()
    return TestClient(app)


def test_api_centroids_includes_dynamic_favourites(fav_app):
    resp = fav_app.get("/api/centroids")
    assert resp.status_code == 200
    data = resp.json()
    assert "dynamic_centroids" in data
    fav = next(
        (d for d in data["dynamic_centroids"] if d["name"] == "favourites"),
        None,
    )
    assert fav is not None
    assert fav["label"] == "Favourites"
    assert fav["source"] == "favourites"
    assert fav["n_images"] is None


def test_centroids_page_renders_dynamic_section_when_empty(fav_app):
    resp = fav_app.get("/centroids")
    assert resp.status_code == 200
    assert "Dynamic" in resp.text
    assert "Favourites" in resp.text
    assert "Favourite a few photos first" in resp.text


def test_centroids_page_shows_count_after_marking(fav_app):
    fav_id = fav_app.app.state.test_seed_ids["a"]
    fav_app.post(f"/api/favorites/{fav_id}")

    resp = fav_app.get("/centroids")
    assert resp.status_code == 200
    assert "Built from" in resp.text
    assert ">1</strong>" in resp.text or "1 photo" in resp.text


def test_search_by_favourites_centroid_uses_computed_vector(fav_app):
    fav_id = fav_app.app.state.test_seed_ids["a"]
    fav_app.post(f"/api/favorites/{fav_id}")
    fav_app.get("/api/centroids")

    resp = fav_app.get("/api/centroids/favourites/search?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["centroid"] == "favourites"
    assert len(data["results"]) >= 1
    ids = {r["id"] for r in data["results"]}
    assert fav_id in ids


def test_search_by_unknown_centroid_returns_404(fav_app):
    resp = fav_app.get("/api/centroids/not-a-real-centroid/search?limit=5")
    assert resp.status_code == 404


def test_mark_favorite_invalidates_centroid_cache(fav_app):
    import search.app as _app_mod

    fav_id = fav_app.app.state.test_seed_ids["a"]
    fav_app.post(f"/api/favorites/{fav_id}")
    fav_app.get("/api/centroids/favourites/search?limit=1")
    assert _app_mod._dynamic_centroids.is_empty("favourites") is False

    fav_app.post(f"/api/favorites/{fav_app.app.state.test_seed_ids['b']}")
    assert _app_mod._dynamic_centroids.cached_n_images("favourites") is None


def test_unmark_favorite_invalidates_centroid_cache(fav_app):
    import search.app as _app_mod

    fav_id = fav_app.app.state.test_seed_ids["a"]
    fav_app.post(f"/api/favorites/{fav_id}")
    fav_app.get("/api/centroids/favourites/search?limit=1")
    assert _app_mod._dynamic_centroids.cached_n_images("favourites") == 1

    fav_app.delete(f"/api/favorites/{fav_id}")
    assert _app_mod._dynamic_centroids.cached_n_images("favourites") is None


def test_search_by_favourites_centroid_when_empty_returns_404(fav_app):
    resp = fav_app.get("/api/centroids/favourites/search?limit=5")
    assert resp.status_code == 404
    assert "no data yet" in resp.json()["detail"]


def test_orphaned_favourite_excluded_from_centroid(fav_app):
    import search.app as _app_mod
    

    fav_id = fav_app.app.state.test_seed_ids["a"]
    fav_app.post(f"/api/favorites/{fav_id}")
    fav_app.get("/api/centroids")
    data = fav_app.get("/api/centroids").json()
    fav = next(d for d in data["dynamic_centroids"] if d["name"] == "favourites")
    assert fav["n_images"] == 1

    qdrant_client = fav_app.app.state.qdrant_client
    collection = fav_app.app.state.qdrant_collection
    from qdrant_client.http import models as qmodels
    qdrant_client.delete(
        collection_name=collection,
        points_selector=qmodels.PointIdsList(points=[fav_id]),
        wait=True,
    )
    _app_mod._invalidate_favourites_centroid()
    fav_app.get("/api/centroids")
    data = fav_app.get("/api/centroids").json()
    fav = next(d for d in data["dynamic_centroids"] if d["name"] == "favourites")
    assert fav["n_images"] is None
