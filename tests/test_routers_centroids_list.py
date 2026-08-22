"""
tests/test_routers_centroids_list.py — centroids list router contract
(§B2 step 11).

Pins the /api/centroids list endpoint's contract. The search route
(/api/centroids/{name}/search) stays inline — it's a much bigger
extraction that shares state with /api/search.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build(centroid_store, dynamic_centroids):
    from search.routers.centroids_list import build_centroids_list_router
    router = build_centroids_list_router(
        centroid_store=centroid_store,
        dynamic_centroids=dynamic_centroids,
    )
    app = FastAPI()
    app.include_router(router)
    return app


def test_list_with_no_store_returns_empty_static_and_null_model():
    store = None
    dyn = None
    app = _build(store, dyn)
    with TestClient(app) as client:
        resp = client.get("/api/centroids")
    assert resp.status_code == 200
    data = resp.json()
    assert data["centroids"] == []
    assert data["dynamic_centroids"] == []
    assert data["expected_model"] is None
    assert data["expected_feature_dim"] is None


def test_list_with_static_store_includes_metadata():
    store = MagicMock()
    spec = MagicMock()
    spec.as_dict.return_value = {"name": "kpop_vibes", "model": "siglip2", "dim": 1536}
    store.list.return_value = [spec]
    store.expected_model.return_value = "ViT-L-16-SigLIP2-256"
    store.expected_feature_dim.return_value = 1536

    app = _build(store, None)
    with TestClient(app) as client:
        resp = client.get("/api/centroids")
    data = resp.json()
    assert data["centroids"] == [
        {"name": "kpop_vibes", "model": "siglip2", "dim": 1536},
    ]
    assert data["expected_model"] == "ViT-L-16-SigLIP2-256"
    assert data["expected_feature_dim"] == 1536


def test_list_with_dynamic_registry_includes_n_images_count():
    store = None
    dyn = MagicMock()
    spec = MagicMock()
    spec.name = "favourites"
    spec.public_dict.return_value = {"name": "favourites", "n_images": 5}
    dyn.list.return_value = [spec]
    dyn.cached_n_images.return_value = 5
    # Trigger compute (cached) — get_vector is called once during the
    # response build to ensure n_images is populated.
    dyn.get_vector.return_value = [0.0] * 1536

    app = _build(store, dyn)
    with TestClient(app) as client:
        resp = client.get("/api/centroids")
    data = resp.json()
    assert data["dynamic_centroids"] == [
        {"name": "favourites", "n_images": 5},
    ]
    spec.public_dict.assert_called_once_with(5)
    dyn.get_vector.assert_called_once_with("favourites")


def test_list_with_both_static_and_dynamic_returns_both_sections():
    store = MagicMock()
    static_spec = MagicMock()
    static_spec.as_dict.return_value = {"name": "album_X"}
    store.list.return_value = [static_spec]
    store.expected_model.return_value = "m"
    store.expected_feature_dim.return_value = 1536

    dyn = MagicMock()
    dyn_spec = MagicMock()
    dyn_spec.name = "favourites"
    dyn_spec.public_dict.return_value = {"name": "favourites", "n_images": 10}
    dyn.list.return_value = [dyn_spec]
    dyn.cached_n_images.return_value = 10
    dyn.get_vector.return_value = [0.0] * 1536

    app = _build(store, dyn)
    with TestClient(app) as client:
        resp = client.get("/api/centroids")
    data = resp.json()
    assert len(data["centroids"]) == 1
    assert len(data["dynamic_centroids"]) == 1
