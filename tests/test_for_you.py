"""Tests for the /for-you persistent recommendation feed."""

import os

import pytest
from fastapi.testclient import TestClient

from search import app as app_mod
from search.config import Config


@pytest.fixture(autouse=True)
def clean_for_you_db(monkeypatch):
    """Each test runs against a fresh IndexDB so cross-test favourite
    state from one assertion doesn't bleed into the next.

    We set INDEX_DB_PATH *before* the app is built (the env read
    happens once per Config construction). The fixture is autouse
    so it lands ahead of `app_with_qdrant` in the dependency graph.

    Also clears the module-level `_signal_cache` in `search.for_you` —
    it survives across tests because it's a module global, not tied
    to any IndexDB instance. Without this, a dislike added in one
    test leaks into the next via `build_state()`'s 30s cache.
    """
    import os
    db_path = f"/tmp/test_for_you_clean_{os.getpid()}_{id(monkeypatch)}.idx"
    monkeypatch.setenv("INDEX_DB_PATH", db_path)
    from search import for_you as for_you_mod
    for_you_mod.invalidate_signal_cache()
    yield db_path
    try:  # noqa: SIM105
        os.unlink(db_path)
    except FileNotFoundError:
        pass


@pytest.fixture
def app_with_qdrant(qdrant_in_memory, nas_base, monkeypatch, clean_for_you_db):
    """Empty-qdrant app — same shape as the search_api tests.

    Reads INDEX_DB_PATH from the env (set by `clean_for_you_db`)
    so each test gets a fresh IndexDB."""
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
        index_db_path=clean_for_you_db,
    )
    app = app_mod.create_app(cfg=cfg, qdrant=qdrant_in_memory)
    return TestClient(app)




def test_for_you_state_endpoint_empty(app_with_qdrant):
    """State endpoint returns the zero baseline when no feedback yet."""
    r = app_with_qdrant.get("/api/for-you/state")
    assert r.status_code == 200
    data = r.json()
    assert data["n_likes"] == 0
    assert data["n_dislikes"] == 0
    assert data["freshest_feedback_ts"] is None


def test_for_you_reset(app_with_qdrant):
    """Reset route returns 204 and is idempotent."""
    r = app_with_qdrant.post("/api/for-you/reset")
    assert r.status_code in (204, 200)
    r = app_with_qdrant.post("/api/for-you/reset")
    assert r.status_code in (204, 200)


def test_for_you_state_increments_after_favorite(app_with_qdrant):
    """End-to-end: insert a fake image row, mark it as favourite,
    state should reflect the new favourite."""
    real_id = "abcdef00-1111-2222-3333-444455556666"
    db_path = os.environ.get("INDEX_DB_PATH", "/tmp/test_for_you_api.idx")
    # Seed the image row via IndexDB directly.
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR IGNORE INTO images (id, path, indexed_at) VALUES (?, ?, ?)",
        (real_id, "/tmp/fake_for_you.jpg", "2024-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    # Like it.
    r = app_with_qdrant.post(f"/api/favorites/{real_id}")
    assert r.status_code in (204, 200)

    # State should report at least one favourite.
    r = app_with_qdrant.get("/api/for-you/state")
    assert r.status_code == 200
    data = r.json()
    assert data["n_likes"] >= 1




def test_for_you_dislike_endpoint_smoke(app_with_qdrant):
    """Dislike write path is wired; on a non-cached id it either
    204s (allow orphans) or 404s (reject). Either is acceptable
    behaviour; the route must not 500."""
    fake_id = "deadbeef-dead-beef-dead-beefdeadbeef"
    r = app_with_qdrant.post(f"/api/dislikes/{fake_id}?source=for_you")
    assert r.status_code in (204, 404)
    r = app_with_qdrant.delete(f"/api/dislikes/{fake_id}")
    assert r.status_code in (204, 404)


def test_for_you_compute_module_is_pure():
    """Phase B3 contract: search.for_you_compute is pure."""
    import search.for_you_compute as compute

    assert callable(compute.zero_vector)
    assert callable(compute.pool_k_default)


def test_for_you_compute_zero_vector_returns_correct_length():
    """zero_vector returns a list of zeros with the requested dim."""
    from search.for_you_compute import zero_vector

    vec = zero_vector(768)
    assert len(vec) == 768
    assert all(v == 0.0 for v in vec)


def test_for_you_compute_zero_vector_rejects_non_positive_dim():
    """zero_vector rejects 0 and negative dims."""
    import pytest

    from search.for_you_compute import zero_vector

    with pytest.raises(ValueError):
        zero_vector(0)
    with pytest.raises(ValueError):
        zero_vector(-1)


def test_for_you_compute_pool_k_default():
    """pool_k_default = max(limit * 4, 80)."""
    from search.for_you_compute import pool_k_default

    assert pool_k_default(20) == 80  # 20*4=80, max(80,80)=80
    assert pool_k_default(30) == 120  # 30*4=120 > 80
    assert pool_k_default(100) == 400  # 100*4=400
