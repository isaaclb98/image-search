"""Tests for the /random page and /api/random endpoint."""
from __future__ import annotations

import pytest

from indexer import upsert
from indexer.upsert import VECTOR_DIM


# Reuse the search-side fixture but seed three rows directly into the
# SQLite cache so we don't have to round-trip through Qdrant — the
# random page reads from the cache, not from Qdrant.
@pytest.fixture
def random_app(tmp_path, monkeypatch):
    import sqlite3

    from search.app import create_app
    from search.config import Config

    cfg = Config(
        qdrant_url="memory://",
        qdrant_collection="images_test_random",
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

    # Lazy liveness check (added in fix/dual-store-cleanup) calls
    # Path(path).exists() on every row the random / album routes
    # return. The fixture seeds paths like /photos/a.jpg directly via
    # SQL — they're not real files on disk, so the liveness check
    # correctly filters every row out. Mock the helper to return True
    # so the seeded rows survive.
    #
    # The fix for the UNC-payload bug (2026-08-15) calls resolve_local
    # before _is_path_alive. The fixture's seeded paths don't match
    # the production prefix, so resolve_local would return None and
    # drop every row. Mock it to a no-op identity so the seeded paths
    # round-trip through the filter.
    from pathlib import Path as _Path
    from search import app as _app_mod
    monkeypatch.setattr(
        _app_mod,
        "resolve_local",
        lambda path, *a, **kw: _Path(path) if path else None,
    )
    monkeypatch.setattr(_app_mod, "_is_path_alive", lambda path: True)

    # Seed the SQLite cache directly — the random page reads from
    # there, not from Qdrant, so we can skip the indexer entirely.
    db_path = str(tmp_path / "images.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE images (
          id            TEXT PRIMARY KEY,
          path          TEXT NOT NULL,
          shard         TEXT DEFAULT '',
          collection    TEXT DEFAULT '',
          mtime         INTEGER,
          size          INTEGER,
          indexed_at    TEXT,
          is_favorite   INTEGER DEFAULT 0,
          favorited_at  TEXT,
          width         INTEGER,
          height        INTEGER
        );
        INSERT INTO images (id, path, collection, width, height) VALUES
          ('a', '/photos/a.jpg', 'kpop',    640, 480),
          ('b', '/photos/b.jpg', 'kpop',    800, 600),
          ('c', '/photos/c.jpg', 'portrait', 400, 400),
          ('d', '/photos/d.jpg', 'portrait', 500, 700);
        """
    )
    conn.commit()
    conn.close()

    # The app still wants a Qdrant client + index_db handle, even
    # though /random doesn't use Qdrant. Hand it an in-memory one.
    from qdrant_client import QdrantClient
    client = QdrantClient(location=":memory:")
    upsert.ensure_collection(client, cfg.qdrant_collection, dim=VECTOR_DIM)

    from search.qdrant_client import QdrantSearch
    qdrant = QdrantSearch(client=client, collection=cfg.qdrant_collection, timeout_ms=2000)

    from fastapi.testclient import TestClient
    app = create_app(cfg=cfg, qdrant=qdrant)
    return TestClient(app)


def test_api_random_returns_rows(random_app):
    resp = random_app.get("/api/random?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["view"] == "grid"  # DEFAULT_VIEW
    assert data["query"] == ""
    assert data["centroid"] is None
    assert data["limit"] == 10
    # All four seeded rows are present (no collection filter).
    ids = {r["id"] for r in data["results"]}
    assert ids == {"a", "b", "c", "d"}


def test_api_random_filters_by_collection(random_app):
    resp = random_app.get("/api/random?limit=10&collections=kpop")
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["results"]}
    assert ids == {"a", "b"}


def test_api_random_multi_collection_filter(random_app):
    resp = random_app.get(
        "/api/random?limit=10&collections=kpop&collections=portrait"
    )
    assert resp.status_code == 200
    ids = {r["id"] for r in resp.json()["results"]}
    assert ids == {"a", "b", "c", "d"}


def test_api_random_unknown_collection_returns_empty(random_app):
    resp = random_app.get("/api/random?limit=10&collections=nonexistent")
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_api_random_has_more_true_when_page_filled(random_app):
    """When the request returns exactly `limit` rows, has_more is
    True so the client keeps the sentinel alive and loads more on
    scroll.
    """
    resp = random_app.get("/api/random?limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 2
    assert data["has_more"] is True


def test_api_random_has_more_false_when_collection_exhausted(random_app):
    """When the request returns fewer than `limit` rows, has_more is
    False — the collection (under filter) fits in this page, no
    point keeping the sentinel around.
    """
    # kpop has 2 seeded rows; limit=10 overshoots.
    resp = random_app.get("/api/random?limit=10&collections=kpop")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["results"]) == 2
    assert data["has_more"] is False


def test_api_random_view_feed_echoed(random_app):
    resp = random_app.get("/api/random?limit=10&view=feed")
    assert resp.status_code == 200
    assert resp.json()["view"] == "feed"


def test_api_random_view_invalid_falls_back_to_grid(random_app):
    resp = random_app.get("/api/random?limit=10&view=blah")
    assert resp.status_code == 200
    assert resp.json()["view"] == "grid"


def test_api_random_limit_out_of_range(random_app):
    resp = random_app.get("/api/random?limit=0")
    assert resp.status_code == 400
    resp = random_app.get("/api/random?limit=99999")
    assert resp.status_code == 400


def test_api_random_results_have_basic_fields(random_app):
    """Each random result carries the fields the grid renderer reads:
    id, path, url, score, score_str, is_favorite.
    """
    resp = random_app.get("/api/random?limit=10")
    assert resp.status_code == 200
    for r in resp.json()["results"]:
        assert r["id"]
        assert r["path"]
        assert r["url"]
        assert r["score"] == 0.0
        assert r["score_str"] == ""


def test_random_page_renders(random_app):
    resp = random_app.get("/random")
    assert resp.status_code == 200
    assert "Random" in resp.text
    # View toggle present.
    assert 'data-view="grid"' in resp.text
    assert 'data-view="feed"' in resp.text
    # Shuffle button removed — replaced by infinite scroll on the sentinel.
    assert "shuffle-btn" not in resp.text
    # Sentinel rendered so the IntersectionObserver can fire.
    assert "grid-sentinel" in resp.text
    # SSR data block present so JS view-toggle can re-render
    # without a refetch.
    assert 'id="random-initial-results"' in resp.text
    assert "type=\"application/json\"" in resp.text


def test_random_page_with_collection_filter(random_app):
    resp = random_app.get("/random?collections=kpop")
    assert resp.status_code == 200
    # Resulting page should reflect the filter via the tagline.
    assert "kpop" in resp.text


def test_random_page_view_echoes_in_toggle(random_app):
    """The view toggle's active state matches the URL's view param
    on first paint, so the SSR'd markup doesn't flash.
    """
    resp = random_app.get("/random?view=feed")
    assert resp.status_code == 200
    assert "view-toggle-btn--active" in resp.text
    # The feed button specifically carries the active class.
    feed_section = resp.text.split('data-view="feed"')[0]
    assert "view-toggle-btn--active" in feed_section[-200:]