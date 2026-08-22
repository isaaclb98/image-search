"""
tests/test_routers_collections_centroids.py — collections + centroid reload
router contracts (§B2 steps 6 + 7).

Pins the contract of the small self-contained routers that
wrap a single Qdrant / CentroidStore call. Integration is
verified by the existing test_collections_api.py and the
v2 smoke tests.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_collections_returns_collections_list():
    from search.routers.collections import build_collections_router

    qdrant = MagicMock()
    qdrant.list_collections_with_counts.return_value = [
        {"name": "kpop", "count": 1234},
        {"name": "landscapes", "count": 567},
    ]
    router = build_collections_router(qdrant=qdrant)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.get("/api/collections")
    assert resp.status_code == 200
    data = resp.json()
    assert data["collections"][0]["name"] == "kpop"
    assert data["collections"][1]["count"] == 567


def test_collections_returns_502_on_connection_error():
    from search.routers.collections import build_collections_router

    qdrant = MagicMock()
    qdrant.list_collections_with_counts.side_effect = ConnectionError("nope")
    router = build_collections_router(qdrant=qdrant)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.get("/api/collections")
    assert resp.status_code == 502
    assert resp.json()["error"] == "qdrant_unreachable"
    assert resp.json()["code"] == "qdrant_unreachable"


def test_centroids_reload_returns_count_and_dir():
    from search.routers.centroids import build_centroids_reload_router

    store = MagicMock()
    store.load.return_value = 3
    store.centroids_dir = Path("/var/lib/centroids")
    router = build_centroids_reload_router(centroid_store=store)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.post("/api/centroids/reload")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert data["centroids_dir"] == "/var/lib/centroids"
    store.load.assert_called_once_with()


def test_centroids_reload_returns_503_when_store_missing():
    from search.routers.centroids import build_centroids_reload_router

    router = build_centroids_reload_router(centroid_store=None)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.post("/api/centroids/reload")
    assert resp.status_code == 503
    assert "not initialized" in resp.json()["detail"]


def test_centroids_reload_handles_no_centroids_dir():
    """When the store was created without a directory (e.g. test
    envs), centroids_dir is None and the response surfaces that."""
    from search.routers.centroids import build_centroids_reload_router

    store = MagicMock()
    store.load.return_value = 0
    store.centroids_dir = None
    router = build_centroids_reload_router(centroid_store=store)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.post("/api/centroids/reload")
    assert resp.status_code == 200
    assert resp.json()["centroids_dir"] is None
