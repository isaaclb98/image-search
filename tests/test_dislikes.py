"""Tests for the Dislikes feature: API endpoints, /dislikes page,
and the photo-page dislike button wiring."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from indexer import upsert
from indexer.upsert import VECTOR_DIM
from search import app as app_mod
from search.config import Config

CAT_ID = "11111111-1111-1111-1111-111111111111"
DOG_ID = "22222222-2222-2222-2222-222222222222"


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
        top_k_default=35,
        top_k_max=200,
        query_timeout_ms=2000,
        nas_images_base=str(nas_base),
        path_prefix="",
        web_ui_url="http://localhost:8000",
        log_level="WARNING",
        test_mode=True,
    )
    upsert.ensure_collection(
        qdrant_in_memory.client, qdrant_in_memory.collection, dim=VECTOR_DIM
    )
    upsert.upsert_batch(
        qdrant_in_memory.client,
        qdrant_in_memory.collection,
        [
            (CAT_ID, _mock_embed("cat"), {"id": CAT_ID, "path": str(nas_base / "cat.jpg"), "mtime": 1, "size": 10, "indexed_at": "2026-01-01T00:00:00Z"}),
            (DOG_ID, _mock_embed("dog"), {"id": DOG_ID, "path": str(nas_base / "dog.jpg"), "mtime": 2, "size": 20, "indexed_at": "2026-01-01T00:00:00Z"}),
        ],
        wait=True,
    )
    Image.new("RGB", (8, 8), (255, 0, 0)).save(nas_base / "cat.jpg")
    Image.new("RGB", (8, 8), (0, 255, 0)).save(nas_base / "dog.jpg")

    app_mod.reset_for_tests()
    app = app_mod.create_app(cfg=cfg, qdrant=qdrant_in_memory)
    with TestClient(app) as client:
        yield client
    app_mod.reset_for_tests()


# ---------------- API ----------------

def test_post_dislike_marks_photo(app_with_qdrant):
    r = app_with_qdrant.post(f"/api/dislikes/{CAT_ID}?source=detail")
    assert r.status_code == 204
    data = app_with_qdrant.get("/api/dislikes").json()
    ids = [str(i["id"]) for i in data["items"]]
    assert CAT_ID in ids
    assert data["count"] == 1


def test_dislike_is_idempotent(app_with_qdrant):
    app_with_qdrant.post(f"/api/dislikes/{CAT_ID}?source=detail")
    app_with_qdrant.post(f"/api/dislikes/{CAT_ID}?source=for_you")
    data = app_with_qdrant.get("/api/dislikes").json()
    assert data["count"] == 1


def test_delete_dislike_removes(app_with_qdrant):
    app_with_qdrant.post(f"/api/dislikes/{CAT_ID}")
    r = app_with_qdrant.delete(f"/api/dislikes/{CAT_ID}")
    assert r.status_code == 204
    data = app_with_qdrant.get("/api/dislikes").json()
    assert data["count"] == 0


def test_dislike_list_as_results_shape(app_with_qdrant):
    app_with_qdrant.post(f"/api/dislikes/{CAT_ID}")
    app_with_qdrant.post(f"/api/dislikes/{DOG_ID}")
    r = app_with_qdrant.get("/api/dislikes?as_results=true&limit=10")
    assert r.status_code == 200
    data = r.json()
    assert len(data["results"]) == 2
    assert data["has_more"] is False
    ids = {res["id"] for res in data["results"]}
    assert ids == {CAT_ID, DOG_ID}
    for res in data["results"]:
        assert res["url"]
        assert res["is_favorite"] is False


def test_dislike_validation_errors(app_with_qdrant):
    # non-integer rejected by FastAPI query validation (422)
    assert app_with_qdrant.get("/api/dislikes?limit=abc").status_code == 422
    assert app_with_qdrant.get("/api/dislikes?limit=0").status_code == 400
    assert app_with_qdrant.get("/api/dislikes?offset=-1").status_code == 400


# ---------------- HTML pages ----------------

def test_dislikes_page_renders(app_with_qdrant):
    r = app_with_qdrant.get("/dislikes")
    assert r.status_code == 200
    text = r.text
    assert "dislikes-page" in text
    assert "data-dislikes-page" in text
    # empty state when nothing disliked
    assert "No dislikes yet" in text


def test_dislikes_page_shows_disliked_photo(app_with_qdrant):
    app_with_qdrant.post(f"/api/dislikes/{CAT_ID}")
    r = app_with_qdrant.get("/dislikes")
    assert r.status_code == 200
    # empty state present in DOM but hidden once there are results
    assert "data-dislikes-empty hidden" in r.text.replace("\n", " ") or \
        'data-dislikes-empty\n        hidden' in r.text or "hidden" in r.text.split("data-dislikes-empty")[1][:20]
    assert CAT_ID in r.text


def test_photo_page_has_dislike_button(app_with_qdrant):
    r = app_with_qdrant.get(f"/photo/{CAT_ID}")
    assert r.status_code == 200
    text = r.text
    assert "data-dislike-form" in text
    assert "dislike-toggle" in text
    assert f'action="/api/dislikes/{CAT_ID}"' in text
    # not disliked yet -> off state
    assert 'data-dislike-state="off"' in text


def test_photo_page_dislike_state_after_dislike(app_with_qdrant):
    app_with_qdrant.post(f"/api/dislikes/{CAT_ID}?source=detail")
    r = app_with_qdrant.get(f"/photo/{CAT_ID}")
    assert 'data-dislike-state="on"' in r.text
    assert 'aria-pressed="true"' in r.text


def test_nav_has_dislikes_link(app_with_qdrant):
    r = app_with_qdrant.get("/")
    assert "/dislikes" in r.text


def test_dislike_reflected_in_for_you_state(app_with_qdrant):
    app_with_qdrant.post(f"/api/dislikes/{CAT_ID}")
    data = app_with_qdrant.get("/api/for-you/state").json()
    assert data["n_dislikes"] == 1
    assert CAT_ID in data.get("excluded_ids", []) or data["n_dislikes"] == 1
