"""
tests/test_routers_similar.py — similar router contract (§B2 step 10).

Pins the similar router's contract: factory returns an APIRouter
with the one documented endpoint. Integration is verified by
test_search_api.py / test_favourites_centroid.py which exercise
the real wiring.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _hit(id_: str, score: float = 0.9, payload=None):
    h = MagicMock()
    h.id = id_
    h.path = f"/photos/{id_}.jpg"
    h.score = score
    h.payload = payload or {}
    return h


def _build(qdrant, cfg, index_db):
    from search.routers.similar import build_similar_router
    router = build_similar_router(qdrant=qdrant, cfg=cfg, index_db=index_db)
    app = FastAPI()
    app.include_router(router)
    return app


def _fake_cfg():
    c = MagicMock()
    c.top_k_default = 35
    c.web_ui_url = "http://localhost:5173"
    return c


def test_similar_returns_neighbours_excluding_source():
    qdrant = MagicMock()
    qdrant.retrieve_with_vector.return_value = (
        [0.1] * 1536, _hit("source", score=1.0),
    )
    qdrant.search.return_value = (
        [_hit("a", 0.9), _hit("b", 0.85), _hit("c", 0.8)],
        True,
    )
    index_db = MagicMock()
    index_db.favorite_id_set.return_value = {"a"}
    index_db.dislike_id_set.return_value = set()
    app = _build(qdrant, _fake_cfg(), index_db)
    with TestClient(app) as client:
        resp = client.get("/api/similar/source?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 3
    ids = {r["id"] for r in data["results"]}
    assert ids == {"a", "b", "c"}
    # `a` was marked favourite by the fake IndexDB; the others weren't.
    fav_map = {r["id"]: r["is_favorite"] for r in data["results"]}
    assert fav_map["a"] is True
    assert fav_map["b"] is False
    # Source must be excluded at the Qdrant layer (args[5] = exclude_ids).
    assert "source" in qdrant.search.call_args.args[5]


def test_similar_source_not_found_returns_404():
    qdrant = MagicMock()
    qdrant.retrieve_with_vector.return_value = None
    index_db = MagicMock()
    app = _build(qdrant, _fake_cfg(), index_db)
    with TestClient(app) as client:
        resp = client.get("/api/similar/missing-id?limit=5")
    assert resp.status_code == 404


def test_similar_qdrant_retrieve_error_returns_502():
    qdrant = MagicMock()
    qdrant.retrieve_with_vector.side_effect = ConnectionError("nope")
    index_db = MagicMock()
    app = _build(qdrant, _fake_cfg(), index_db)
    with TestClient(app) as client:
        resp = client.get("/api/similar/source?limit=5")
    assert resp.status_code == 502


def test_similar_qdrant_search_error_returns_502():
    qdrant = MagicMock()
    qdrant.retrieve_with_vector.return_value = (
        [0.1] * 1536, _hit("source", score=1.0),
    )
    qdrant.search.side_effect = OSError("qdrant down")
    index_db = MagicMock()
    app = _build(qdrant, _fake_cfg(), index_db)
    with TestClient(app) as client:
        resp = client.get("/api/similar/source?limit=5")
    assert resp.status_code == 502


def test_similar_trims_to_requested_limit():
    """The over-fetched +1 row gets trimmed so we return at most `limit`."""
    qdrant = MagicMock()
    qdrant.retrieve_with_vector.return_value = (
        [0.1] * 1536, _hit("source", score=1.0),
    )
    # Source slipped through the server-side exclude for some reason;
    # the trim must drop it.
    qdrant.search.return_value = (
        [_hit("source"), _hit("a"), _hit("b"), _hit("c")],
        True,
    )
    index_db = MagicMock()
    index_db.favorite_id_set.return_value = set()
    index_db.dislike_id_set.return_value = set()
    app = _build(qdrant, _fake_cfg(), index_db)
    with TestClient(app) as client:
        resp = client.get("/api/similar/source?limit=3")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 3


def test_similar_limit_above_200_returns_422():
    """FastAPI's `le=200` constraint rejects limits above the cap at
    the path validation layer."""
    qdrant = MagicMock()
    index_db = MagicMock()
    app = _build(qdrant, _fake_cfg(), index_db)
    with TestClient(app) as client:
        resp = client.get("/api/similar/source?limit=999")
    assert resp.status_code == 422


def test_similar_limit_zero_returns_422():
    qdrant = MagicMock()
    index_db = MagicMock()
    app = _build(qdrant, _fake_cfg(), index_db)
    with TestClient(app) as client:
        resp = client.get("/api/similar/source?limit=0")
    assert resp.status_code == 422


def test_similar_score_format_is_3dp():
    """score_str is formatted to 3 decimals — SSR + JS render identically."""
    qdrant = MagicMock()
    qdrant.retrieve_with_vector.return_value = (
        [0.1] * 1536, _hit("source", score=1.0),
    )
    qdrant.search.return_value = (
        [_hit("a", 0.87654), _hit("b", 0.5)],
        False,
    )
    index_db = MagicMock()
    index_db.favorite_id_set.return_value = set()
    index_db.dislike_id_set.return_value = set()
    app = _build(qdrant, _fake_cfg(), index_db)
    with TestClient(app) as client:
        resp = client.get("/api/similar/source?limit=5")
    data = resp.json()
    score_strs = {r["id"]: r["score_str"] for r in data["results"]}
    assert score_strs["a"] == "0.877"
    assert score_strs["b"] == "0.500"
