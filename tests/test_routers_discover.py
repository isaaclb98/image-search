"""
tests/test_routers_discover.py — discover router contract (§B2 step 8).

Pins the discover router's contract: factory returns an APIRouter
with the two documented endpoints. Integration is verified by
tests/test_discover.py which exercises the real wiring.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from search.models import DiscoveryImage, DiscoveryPair


@pytest.fixture
def fake_qdrant():
    return MagicMock()


@pytest.fixture
def fake_cfg():
    c = MagicMock()
    c.web_ui_url = "http://localhost:5173"
    c.discover_seed_phase_min = 5
    c.discover_pool_k = 200
    c.discover_recent_window_k = 60
    c.discover_recent_decay = 0.97
    c.discover_seed_phase_size = 8
    c.discover_pair_pool_k = 200
    return c


@pytest.fixture
def fake_index_db():
    return MagicMock()


def _build(fake_qdrant, fake_cfg, fake_index_db):
    from search.routers.discover import build_discover_router
    router = build_discover_router(
        qdrant=fake_qdrant,
        cfg=fake_cfg,
        index_db=fake_index_db,
    )
    app = FastAPI()
    app.include_router(router)
    return app


def _mock_pair() -> DiscoveryPair:
    """Return a DiscoveryPair with empty URLs (router must hydrate)."""
    return DiscoveryPair(
        round=1,
        left=DiscoveryImage(id="left-id", path="/left.jpg", url=""),
        right=DiscoveryImage(id="right-id", path="/right.jpg", url=""),
        source="random",
    )


def test_discover_start_returns_session_id_and_pair(fake_qdrant, fake_cfg, fake_index_db):
    pair = _mock_pair()
    with patch("search.discover.start_session", return_value=("sess-1", pair)) as m:
        app = _build(fake_qdrant, fake_cfg, fake_index_db)
        with TestClient(app) as client:
            resp = client.post("/api/discover/start")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "sess-1"
    # Pair URLs got hydrated.
    assert data["pair"]["left"]["url"].startswith("http")
    assert data["pair"]["right"]["url"].startswith("http")
    m.assert_called_once()


def test_discover_start_hydrates_with_web_ui_url(fake_qdrant, fake_cfg, fake_index_db):
    pair = _mock_pair()
    fake_cfg.web_ui_url = "http://photos.example.test:9000"
    with patch("search.discover.start_session", return_value=("s", pair)):
        app = _build(fake_qdrant, fake_cfg, fake_index_db)
        with TestClient(app) as client:
            resp = client.post("/api/discover/start")
    assert resp.json()["pair"]["left"]["url"].startswith("http://photos.example.test:9000")


def test_discover_start_returns_502_on_qdrant_error(fake_qdrant, fake_cfg, fake_index_db):
    with patch(
        "search.discover.start_session",
        side_effect=ConnectionError("qdrant down"),
    ):
        app = _build(fake_qdrant, fake_cfg, fake_index_db)
        with TestClient(app) as client:
            resp = client.post("/api/discover/start")
    assert resp.status_code == 502


def test_discover_start_response_shape(fake_qdrant, fake_cfg, fake_index_db):
    """start_session returns a non-null pair — the route must always
    hand the client something to look at."""
    pair = _mock_pair()
    with patch("search.discover.start_session", return_value=("sess-1", pair)):
        app = _build(fake_qdrant, fake_cfg, fake_index_db)
        with TestClient(app) as client:
            resp = client.post("/api/discover/start")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "sess-1"
    assert "left" in data["pair"]
    assert "right" in data["pair"]


def test_discover_pick_returns_next_pair_and_progress(
    fake_qdrant, fake_cfg, fake_index_db,
):
    pair = _mock_pair()
    session = MagicMock()
    session.liked = ["a", "b", "c"]
    session.round = 2
    with patch("search.discover.submit_pick", return_value=pair), \
         patch("search.discover.get_session", return_value=session):
        app = _build(fake_qdrant, fake_cfg, fake_index_db)
        with TestClient(app) as client:
            resp = client.post(
                "/api/discover/pick?session_id=sess-1&image_id=abc",
            )
    assert resp.status_code == 200
    data = resp.json()
    assert data["round"] == 2
    assert data["liked_count"] == 3
    assert data["pair"]["left"]["url"].startswith("http")


def test_discover_pick_returns_zero_progress_when_session_gone(
    fake_qdrant, fake_cfg, fake_index_db,
):
    """get_session returning None is the 'session ended' path."""
    pair = _mock_pair()
    with patch("search.discover.submit_pick", return_value=pair), \
         patch("search.discover.get_session", return_value=None):
        app = _build(fake_qdrant, fake_cfg, fake_index_db)
        with TestClient(app) as client:
            resp = client.post(
                "/api/discover/pick?session_id=fake&image_id=abc",
            )
    assert resp.status_code == 200
    assert resp.json()["round"] == 0
    assert resp.json()["liked_count"] == 0


def test_discover_pick_returns_502_on_qdrant_error(fake_qdrant, fake_cfg, fake_index_db):
    with patch(
        "search.discover.submit_pick",
        side_effect=OSError("qdrant down"),
    ):
        app = _build(fake_qdrant, fake_cfg, fake_index_db)
        with TestClient(app) as client:
            resp = client.post(
                "/api/discover/pick?session_id=sess-1&image_id=abc",
            )
    assert resp.status_code == 502
