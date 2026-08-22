"""
tests/test_routers_for_you.py — for-you router contract (§B2 step 5).

Pins the for-you router module's contract: the factory function
returns an APIRouter with the three documented endpoints. Tests
run against a MagicMock-backed index_db / qdrant without the
full create_app(); integration is verified by tests/test_for_you.py
which exercises the real wiring.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def fake_index_db():
    db = MagicMock()
    db.list_favorite_ids.return_value = []
    db.list_dislike_ids.return_value = []
    db.reset_feedback.return_value = None
    return db


@pytest.fixture
def fake_state():
    """MagicMock that quacks like search.for_you.ForYouState."""
    s = MagicMock()
    s.n_likes = 3
    s.n_dislikes = 1
    s.freshest_feedback_ts = "2026-08-22T12:00:00Z"
    return s


@pytest.fixture
def fake_qdrant():
    return MagicMock()


@pytest.fixture
def invalidate():
    return MagicMock()


def _build(fake_index_db, fake_qdrant, invalidate, *, state=None, hits=None):
    # Stub out the for_you compute functions so we don't need real Qdrant.
    import search.for_you as for_you_mod
    from search.routers.for_you import build_for_you_router
    if state is None:
        state = MagicMock(n_likes=0, n_dislikes=0, freshest_feedback_ts=None)
    hits = hits if hits is not None else []
    for_you_mod.build_state = MagicMock(return_value=state)
    for_you_mod.rank = MagicMock(return_value=hits)
    router = build_for_you_router(
        index_db=fake_index_db,
        qdrant=fake_qdrant,
        invalidate_favourites_centroid=invalidate,
        invalidate_for_you_signal=invalidate,
    )
    app = FastAPI()
    app.include_router(router)
    return app


def test_state_returns_signal_snapshot(fake_index_db, fake_qdrant, invalidate, fake_state):
    app = _build(fake_index_db, fake_qdrant, invalidate, state=fake_state)
    with TestClient(app) as client:
        resp = client.get("/api/for-you/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_likes"] == 3
    assert data["n_dislikes"] == 1
    assert data["freshest_feedback_ts"] == "2026-08-22T12:00:00Z"


def test_state_freshest_ts_can_be_none(fake_index_db, fake_qdrant, invalidate):
    """Empty feedback set returns null timestamp."""
    empty_state = MagicMock(n_likes=0, n_dislikes=0, freshest_feedback_ts=None)
    app = _build(fake_index_db, fake_qdrant, invalidate, state=empty_state)
    with TestClient(app) as client:
        resp = client.get("/api/for-you/state")
    assert resp.status_code == 200
    assert resp.json()["freshest_feedback_ts"] is None


def test_feed_returns_results_shape(fake_index_db, fake_qdrant, invalidate, fake_state):
    """Feed wraps hits in the documented JSON shape."""
    # Hit objects with id/path/payload.
    h1 = MagicMock()
    h1.id = "abc"
    h1.path = "/photos/a.jpg"
    h1.score = 0.9
    h1.payload = {"blurhash": "LKO2?U%2Tw=w]~RBVZRi};RPxuwH"}
    h2 = MagicMock()
    h2.id = "def"
    h2.path = "/photos/b.jpg"
    h2.score = 0.8
    h2.payload = {}
    app = _build(fake_index_db, fake_qdrant, invalidate, state=fake_state, hits=[h1, h2])
    with TestClient(app) as client:
        resp = client.get("/api/for-you/feed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_likes"] == 3
    assert data["n_dislikes"] == 1
    assert len(data["results"]) == 2
    assert data["results"][0]["id"] == "abc"
    assert data["results"][0]["score"] == 0.9
    assert data["results"][0]["blurhash"] == "LKO2?U%2Tw=w]~RBVZRi};RPxuwH"
    # Favourites set is empty in this test → both rows unfavourited.
    assert data["results"][0]["is_favorite"] is False


def test_feed_marks_favourites(fake_index_db, fake_qdrant, invalidate, fake_state):
    fake_index_db.list_favorite_ids.return_value = ["abc"]
    h1 = MagicMock(id="abc", path="/a.jpg", score=0.9, payload={})
    h2 = MagicMock(id="def", path="/b.jpg", score=0.8, payload={})
    app = _build(fake_index_db, fake_qdrant, invalidate, state=fake_state, hits=[h1, h2])
    with TestClient(app) as client:
        resp = client.get("/api/for-you/feed")
    assert resp.status_code == 200
    results = resp.json()["results"]
    fav_map = {r["id"]: r["is_favorite"] for r in results}
    assert fav_map["abc"] is True
    assert fav_map["def"] is False


def test_feed_clamps_limit_to_100(fake_index_db, fake_qdrant, invalidate, fake_state):
    """limit above 100 gets clamped silently — the route's documented behaviour."""
    import search.for_you as for_you_mod
    captured = {}

    def _rank(*, state, fav_ids, dis_ids, qdrant, limit, **_):
        captured["limit"] = limit
        return []

    for_you_mod.build_state = MagicMock(return_value=fake_state)
    for_you_mod.rank = _rank
    from search.routers.for_you import build_for_you_router
    router = build_for_you_router(
        index_db=fake_index_db,
        qdrant=fake_qdrant,
        invalidate_favourites_centroid=invalidate,
        invalidate_for_you_signal=invalidate,
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.get("/api/for-you/feed?limit=999")
    assert resp.status_code == 200
    assert captured["limit"] == 100


def test_reset_calls_db_and_invalidators(fake_index_db, fake_qdrant, invalidate):
    app = _build(fake_index_db, fake_qdrant, invalidate)
    with TestClient(app) as client:
        resp = client.post("/api/for-you/reset")
    assert resp.status_code == 204
    fake_index_db.reset_feedback.assert_called_once_with()
    assert invalidate.call_count == 2
