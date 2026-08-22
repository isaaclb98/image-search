"""
tests/test_routers_random.py — random router contract (§B2 step 9).

Pins the random router's contract: factory returns an APIRouter
with the one documented endpoint. Integration is verified by
the existing test_random_api.py suite.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build(index_db, cfg):
    from search.routers.random import build_random_router
    router = build_random_router(index_db=index_db, cfg=cfg)
    app = FastAPI()
    app.include_router(router)
    return app


def _fake_cfg():
    c = MagicMock()
    c.top_k_default = 35
    c.default_view = "grid"
    c.web_ui_url = "http://localhost:5173"
    return c


def test_random_returns_results_shape():
    index_db = MagicMock()
    index_db.pick_random_rows.return_value = [
        {"id": "abc", "path": "/photos/a.jpg", "is_favorite": 0,
         "is_disliked": 0, "width": 800, "height": 600,
         "blurhash": "LKO2?U%2Tw=w]~RBVZRi};RPxuwH"},
        {"id": "def", "path": "/photos/b.jpg", "is_favorite": 1,
         "is_disliked": 0, "width": 1024, "height": 768, "blurhash": None},
    ]
    app = _build(index_db, _fake_cfg())
    with TestClient(app) as client:
        resp = client.get("/api/random?limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["limit"] == 2
    assert data["has_more"] is True  # filled the page
    assert len(data["results"]) == 2
    r0 = data["results"][0]
    assert r0["id"] == "abc"
    assert r0["is_favorite"] is False
    assert r0["is_disliked"] is False
    assert r0["width"] == 800
    r1 = data["results"][1]
    assert r1["is_favorite"] is True
    assert r1["blurhash"] is None


def test_random_marks_has_more_false_when_underfilled():
    """When the picker returns fewer than `limit`, the page is exhausted."""
    index_db = MagicMock()
    index_db.pick_random_rows.return_value = [
        {"id": "abc", "path": "/photos/a.jpg", "is_favorite": 0,
         "is_disliked": 0, "width": None, "height": None, "blurhash": None},
    ]
    app = _build(index_db, _fake_cfg())
    with TestClient(app) as client:
        resp = client.get("/api/random?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 1
    assert data["has_more"] is False


def test_random_dedupes_ids_across_picks():
    """If two over-fetch attempts return overlapping ids, the route
    must dedupe so the page holds `limit` distinct rows."""
    index_db = MagicMock()
    # First call returns two ids; second call returns the same plus one new.
    index_db.pick_random_rows.side_effect = [
        [{"id": "a", "path": "/a", "is_favorite": 0, "is_disliked": 0,
          "width": None, "height": None, "blurhash": None},
         {"id": "b", "path": "/b", "is_favorite": 0, "is_disliked": 0,
          "width": None, "height": None, "blurhash": None}],
        [{"id": "b", "path": "/b", "is_favorite": 0, "is_disliked": 0,
          "width": None, "height": None, "blurhash": None},
         {"id": "c", "path": "/c", "is_favorite": 0, "is_disliked": 0,
          "width": None, "height": None, "blurhash": None}],
    ]
    app = _build(index_db, _fake_cfg())
    with TestClient(app) as client:
        resp = client.get("/api/random?limit=3")
    assert resp.status_code == 200
    data = resp.json()
    ids = {r["id"] for r in data["results"]}
    assert ids == {"a", "b", "c"}


def test_random_limit_above_max_returns_400():
    index_db = MagicMock()
    app = _build(index_db, _fake_cfg())
    with TestClient(app) as client:
        resp = client.get("/api/random?limit=99999")
    assert resp.status_code == 400
    assert resp.json()["error"] == "bad_request"


def test_random_limit_zero_returns_400():
    index_db = MagicMock()
    app = _build(index_db, _fake_cfg())
    with TestClient(app) as client:
        resp = client.get("/api/random?limit=0")
    assert resp.status_code == 400


def test_random_limit_negative_returns_400():
    index_db = MagicMock()
    app = _build(index_db, _fake_cfg())
    with TestClient(app) as client:
        resp = client.get("/api/random?limit=-5")
    assert resp.status_code == 400


def test_random_filters_empty_collection_strings():
    """Empty / whitespace-only collection strings are dropped silently.

    FastAPI's Query list parser drops empty values; whitespace-only
    ones arrive but the handler's strip+dedup pass removes them.
    Either way the route must not crash.
    """
    index_db = MagicMock()
    index_db.pick_random_rows.return_value = []
    app = _build(index_db, _fake_cfg())
    with TestClient(app) as client:
        resp = client.get(
            "/api/random?collection=kpop&collection=kpop&collection=%20%20",
        )
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_random_returns_500_on_internal_error():
    index_db = MagicMock()
    index_db.pick_random_rows.side_effect = RuntimeError("db wedged")
    app = _build(index_db, _fake_cfg())
    with TestClient(app) as client:
        resp = client.get("/api/random?limit=5")
    assert resp.status_code == 500
    assert resp.json()["error"] == "internal_error"
