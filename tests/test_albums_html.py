"""Smoke tests for the albums HTML pages.

These don't validate the full rendered output — just that the
pages render without 500'ing in the empty state and with
albums present. The data validation lives in test_albums_api.py;
this file guards the template wiring only.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from indexer import upsert
from indexer.upsert import VECTOR_DIM
from search import app as app_mod
from search.config import Config


CAT_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def app_with_qdrant(qdrant_in_memory, nas_base):
    from PIL import Image
    from search.text_encoder import _mock_embed

    cfg = Config(
        qdrant_url="memory://",
        qdrant_collection=qdrant_in_memory.collection,
        qdrant_api_key=None,
        model_name="mock",
        model_revision="",
        device="cpu",
        top_k_default=50,
        top_k_max=200,
        query_timeout_ms=2000,
        nas_images_base=str(nas_base),
        path_prefix="",
        web_ui_url="http://localhost:8000",
        log_level="WARNING",
        test_mode=True,
    )
    upsert.ensure_collection(qdrant_in_memory.client, qdrant_in_memory.collection, dim=VECTOR_DIM)
    upsert.upsert_batch(
        qdrant_in_memory.client,
        qdrant_in_memory.collection,
        [(CAT_ID, _mock_embed("cat"), {
            "id": CAT_ID, "path": str(nas_base / "cat.jpg"),
            "mtime": 1, "size": 10, "indexed_at": "2026-01-01T00:00:00Z",
        })],
        wait=True,
    )
    Image.new("RGB", (8, 8), (255, 0, 0)).save(nas_base / "cat.jpg")

    app_mod.reset_for_tests()
    app = app_mod.create_app(cfg=cfg, qdrant=qdrant_in_memory)
    with TestClient(app) as client:
        yield client
    app_mod.reset_for_tests()


# ---------------- /albums index ----------------


def test_albums_index_renders_empty(app_with_qdrant):
    resp = app_with_qdrant.get("/albums")
    assert resp.status_code == 200
    assert "Albums" in resp.text
    assert "Create album" in resp.text
    assert "No albums yet" in resp.text


def test_albums_index_lists_existing_albums(app_with_qdrant):
    app_with_qdrant.post("/api/albums", json={"name": "studio portraits"})
    app_with_qdrant.post("/api/albums", json={"name": "warm light"})

    resp = app_with_qdrant.get("/albums")
    assert resp.status_code == 200
    assert "studio portraits" in resp.text
    assert "warm light" in resp.text


def test_albums_index_shows_member_count(app_with_qdrant):
    album_id = app_with_qdrant.post(
        "/api/albums", json={"name": "test"},
    ).json()["id"]
    app_with_qdrant.post(f"/api/albums/{album_id}/members/{CAT_ID}")

    resp = app_with_qdrant.get("/albums")
    assert "1 photo" in resp.text


def test_albums_index_cover_is_first_member(app_with_qdrant):
    """The card thumbnail on /albums is the first photo *added* to
    the album, not the explicit cover_favorite_id."""
    album_id = app_with_qdrant.post(
        "/api/albums", json={"name": "first wins"},
    ).json()["id"]
    app_with_qdrant.post(f"/api/albums/{album_id}/members/{CAT_ID}")

    resp = app_with_qdrant.get("/albums")
    assert resp.status_code == 200
    # Cover thumbnail should point to /photo/{CAT_ID}/raw (the first
    # and only member). The data-album-cover div wraps the <img>;
    # check that the src attribute is present for the right id.
    assert f'/photo/{CAT_ID}/raw' in resp.text


def test_albums_index_cover_shows_placeholder_when_empty(app_with_qdrant):
    """An album with no members has no cover — show the placeholder."""
    app_with_qdrant.post("/api/albums", json={"name": "empty"})

    resp = app_with_qdrant.get("/albums")
    assert resp.status_code == 200
    assert "album-card__cover-placeholder" in resp.text


# ---------------- /albums/{id} detail ----------------


def test_album_detail_renders_empty(app_with_qdrant):
    album_id = app_with_qdrant.post(
        "/api/albums", json={"name": "empty"},
    ).json()["id"]

    resp = app_with_qdrant.get(f"/albums/{album_id}")
    assert resp.status_code == 200
    assert "empty" in resp.text
    assert "No photos in this album yet" in resp.text


def test_album_detail_renders_members(app_with_qdrant):
    album_id = app_with_qdrant.post(
        "/api/albums", json={"name": "with members"},
    ).json()["id"]
    app_with_qdrant.post(f"/api/albums/{album_id}/members/{CAT_ID}")

    resp = app_with_qdrant.get(f"/albums/{album_id}")
    assert resp.status_code == 200
    assert "with members" in resp.text
    assert "1 photo" in resp.text


def test_album_detail_unknown_returns_404(app_with_qdrant):
    resp = app_with_qdrant.get("/albums/9999")
    assert resp.status_code == 404


# ---------------- photo page album pills ----------------


def test_photo_page_shows_album_pills(app_with_qdrant):
    album_id = app_with_qdrant.post(
        "/api/albums", json={"name": "studio"},
    ).json()["id"]
    app_with_qdrant.post(f"/api/albums/{album_id}/members/{CAT_ID}")

    resp = app_with_qdrant.get(f"/photo/{CAT_ID}")
    assert resp.status_code == 200
    assert "Albums" in resp.text
    assert "studio" in resp.text
    # Pill rendered as 'on' since this photo is in the album
    assert "album-pill--on" in resp.text


def test_photo_page_shows_pills_off_when_not_in_album(app_with_qdrant):
    app_with_qdrant.post("/api/albums", json={"name": "studio"})

    resp = app_with_qdrant.get(f"/photo/{CAT_ID}")
    assert resp.status_code == 200
    assert "studio" in resp.text
    # No album-pill--on since the photo isn't in any album
    assert "album-pill--on" not in resp.text


def test_photo_page_shows_empty_state_when_no_albums(app_with_qdrant):
    resp = app_with_qdrant.get(f"/photo/{CAT_ID}")
    assert resp.status_code == 200
    assert "Create an album" in resp.text


# ---------------- nav link ----------------


def test_albums_link_in_nav(app_with_qdrant):
    """The Albums nav link should appear on every page (base.html)."""
    resp = app_with_qdrant.get("/")
    assert resp.status_code == 200
    assert 'href="/albums"' in resp.text