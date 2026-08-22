"""
tests/test_routers_saved_searches.py — saved-searches router contract (§B2 step 2).

Pins the saved-searches router's contract: the factory function
returns an `APIRouter` that handles CRUD for `/api/saved-searches`
with the documented response shapes. Each endpoint is testable
against a `MagicMock`-backed `IndexDB` without the full `create_app()`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def fake_index_db():
    db = MagicMock()
    db.create_saved_search.return_value = {
        "id": 1, "name": "vacation",
        "positives": ["beach"], "negatives": [],
        "created_at": "2026-08-22T00:00:00Z",
    }
    db.list_saved_searches.return_value = (
        [{
            "id": 1, "name": "vacation",
            "positives": ["beach"], "negatives": [],
            "created_at": "2026-08-22T00:00:00Z",
        }],
        1,
    )
    db.get_saved_search.return_value = {
        "id": 1, "name": "vacation",
        "positives": ["beach"], "negatives": [],
        "created_at": "2026-08-22T00:00:00Z",
    }
    db.delete_saved_search.return_value = True
    return db


def _bad_request(detail: str):
    """Minimal bad_request stub matching the app's helper contract."""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=400,
        content={"error": "bad_request", "detail": detail, "code": "bad_request"},
    )


def test_build_router_returns_api_router(fake_index_db):
    from search.routers.saved_searches import build_saved_searches_router

    router = build_saved_searches_router(
        index_db=fake_index_db,
    )
    assert isinstance(router, APIRouter)


def test_router_routes_registered(fake_index_db):
    """The router owns exactly the 4 saved-searches CRUD endpoints."""
    from search.routers.saved_searches import build_saved_searches_router

    router = build_saved_searches_router(
        index_db=fake_index_db,
    )
    # FastAPI/Starlette route attributes differ across versions;
    # stringify the route's path/methods to make the test stable.
    paths = []
    for r in router.routes:
        path = getattr(r, "path", None) or str(r)
        paths.append(path)
    assert "/api/saved-searches" in paths
    assert "/api/saved-searches/{saved_id}" in paths


def test_create_saved_search_calls_index_db(fake_index_db):
    from search.routers.saved_searches import build_saved_searches_router

    router = build_saved_searches_router(
        index_db=fake_index_db,
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.post(
            "/api/saved-searches",
            json={"name": "vacation", "positives": ["beach"], "negatives": []},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "vacation"
    fake_index_db.create_saved_search.assert_called_once()


def test_create_saved_search_empty_name_returns_400(fake_index_db):
    from search.routers.saved_searches import build_saved_searches_router

    router = build_saved_searches_router(
        index_db=fake_index_db,
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.post(
            "/api/saved-searches",
            json={"name": "   ", "positives": ["beach"], "negatives": []},
        )
    assert resp.status_code == 400


def test_create_saved_search_empty_prompts_returns_400(fake_index_db):
    from search.routers.saved_searches import build_saved_searches_router

    router = build_saved_searches_router(
        index_db=fake_index_db,
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.post(
            "/api/saved-searches",
            json={"name": "vacation", "positives": [], "negatives": []},
        )
    assert resp.status_code == 400


def test_create_saved_search_unique_conflict_returns_409(fake_index_db):
    from search.routers.saved_searches import build_saved_searches_router

    fake_index_db.create_saved_search.side_effect = ValueError("name already exists")
    router = build_saved_searches_router(
        index_db=fake_index_db,
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.post(
            "/api/saved-searches",
            json={"name": "vacation", "positives": ["beach"], "negatives": []},
        )
    assert resp.status_code == 409


def test_get_saved_search_returns_404_when_missing(fake_index_db):
    from search.routers.saved_searches import build_saved_searches_router

    fake_index_db.get_saved_search.return_value = None
    router = build_saved_searches_router(
        index_db=fake_index_db,
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.get("/api/saved-searches/999")
    assert resp.status_code == 404


def test_delete_saved_search_returns_404_when_missing(fake_index_db):
    from search.routers.saved_searches import build_saved_searches_router

    fake_index_db.delete_saved_search.return_value = False
    router = build_saved_searches_router(
        index_db=fake_index_db,
    )
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        resp = client.delete("/api/saved-searches/999")
    assert resp.status_code == 404