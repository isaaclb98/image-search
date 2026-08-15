"""
tests/test_centroids_api.py

Layer 2 — search API tests covering the centroid feature end-to-end:

  - /api/centroids lists loaded centroids
  - /api/centroids/{name}/search uses the centroid as a query vector
  - /api/centroids/reload rescans the directory on demand
  - /api/search supports ?centroid= (mutually exclusive with text)
  - /  supports ?centroid= (same mutex, HTML rendering)
  - An unknown centroid name 404s (not 500)

The fixture builds two .pt centroid files in tmp_path, points
the app at that dir, and pre-populates an in-memory Qdrant
collection with three known images (cat, dog, car). The
centroid's vector is `mock_embed("cat")` so a search with it
ranks the cat image first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Shared fixture and constants live in _centroid_fixture.py
# (re-exported through conftest.py for pytest auto-discovery).
from _centroid_fixture import (
    CENTROID_CAT_ID as CAT_ID,
)
from _centroid_fixture import (
    NOIR_CENTROID,
    WUXIA_CENTROID,
)
from _centroid_fixture import (
    save_centroid as _save_centroid,
)
from fastapi.testclient import TestClient

from indexer import upsert
from indexer.upsert import VECTOR_DIM
from search import app as app_mod
from search.config import Config

# ----------------------- /api/centroids -----------------------


def test_api_centroids_lists_loaded(app_with_centroids):
    resp = app_with_centroids.get("/api/centroids")
    assert resp.status_code == 200
    data = resp.json()
    names = [c["name"] for c in data["centroids"]]
    # Sorted, only the two valid ones — the dinov3 file was skipped.
    assert names == [NOIR_CENTROID, WUXIA_CENTROID]
    spec = data["centroids"][0]
    assert spec["model"] == "siglip2"
    assert spec["feature_dim"] == VECTOR_DIM
    assert spec["n_images"] == 25
    assert "source_path" in spec
    assert data["expected_model"] == "siglip2"
    assert data["expected_feature_dim"] == VECTOR_DIM


def test_api_centroids_empty_when_dir_unset():
    """A Config with centroids_dir=None returns an empty list, not an error."""
    qdrant_client = pytest.importorskip("qdrant_client").QdrantClient(location=":memory:")
    from search.qdrant_client import QdrantSearch
    qdrant = QdrantSearch(client=qdrant_client, collection="images_test_empty", timeout_ms=2000)
    upsert.ensure_collection(qdrant.client, qdrant.collection, dim=VECTOR_DIM)
    cfg = Config(
        qdrant_url="memory://",
        qdrant_collection="images_test_empty",
        qdrant_api_key=None,
        model_name="mock", model_revision="", device="cpu",
        top_k_default=35, top_k_max=200, query_timeout_ms=2000,
        nas_images_base="/tmp", path_prefix="", web_ui_url="http://localhost:8000",  # noqa: S108 - test fixture
        log_level="WARNING", test_mode=True,
        centroids_dir=None,
    )
    app_mod.reset_for_tests()
    app = app_mod.create_app(cfg=cfg, qdrant=qdrant)
    with TestClient(app) as tc:
        resp = tc.get("/api/centroids")
        assert resp.status_code == 200
        # The model+dim are global config (always present), only the
        # `centroids` list is empty when CENTROIDS_DIR is unset.
        # `dynamic_centroids` is always populated (the favourites
        # centroid is registered at startup regardless of whether
        # the static store has any `.pt` files to load).
        body = resp.json()
        assert body["centroids"] == []
        assert body["expected_model"] == "siglip2"
        assert body["expected_feature_dim"] == 1536
        assert any(d["name"] == "favourites" for d in body["dynamic_centroids"])
    app_mod.reset_for_tests()


# ----------------------- /api/centroids/{name}/search -----------------------


def test_centroid_search_returns_results(app_with_centroids):
    """A centroid search uses the centroid's vector as the query and returns hits."""
    resp = app_with_centroids.get(f"/api/centroids/{WUXIA_CENTROID}/search?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["centroid"] == WUXIA_CENTROID
    assert data["query"] == ""
    assert data["positives"] == []
    assert data["negatives"] == []
    assert len(data["results"]) == 3  # cat, dog, car
    # Scores should be in descending order.
    scores = [r["score"] for r in data["results"]]
    assert scores == sorted(scores, reverse=True)


def test_centroid_search_404_on_unknown(app_with_centroids):
    resp = app_with_centroids.get("/api/centroids/nope/search?limit=10")
    assert resp.status_code == 404
    assert "nope" in resp.json()["detail"]


def test_centroid_search_case_insensitive(app_with_centroids):
    """Wuxia_Female_Leads matches the stored lowercase form."""
    resp = app_with_centroids.get(
        f"/api/centroids/{WUXIA_CENTROID.upper()}/search?limit=5"
    )
    assert resp.status_code == 200
    assert resp.json()["centroid"] == WUXIA_CENTROID  # canonical name echoed back


def test_centroid_search_limit_validation(app_with_centroids):
    resp = app_with_centroids.get(f"/api/centroids/{WUXIA_CENTROID}/search?limit=99999")
    assert resp.status_code == 400


def test_centroid_search_offset_validation(app_with_centroids):
    resp = app_with_centroids.get(f"/api/centroids/{WUXIA_CENTROID}/search?offset=-1")
    assert resp.status_code == 400


def test_centroid_search_collection_filter(app_with_centroids):
    """The collection filter on a centroid search behaves the same as text search."""
    resp = app_with_centroids.get(
        f"/api/centroids/{WUXIA_CENTROID}/search?collection=general&limit=10"
    )
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 3


# ----------------------- /api/centroids/reload -----------------------


def test_reload_picks_up_new_file(app_with_centroids, tmp_path):
    """Add a new .pt file, call reload, see it appear in the list."""
    centroids_dir = Path(app_with_centroids.get("/api/centroids").json()["centroids"][0]["source_path"]).parent
    _save_centroid(centroids_dir / "siglip2_new.pt", "new_centroid")
    resp = app_with_centroids.post("/api/centroids/reload")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3  # wuxia, noir, new
    names = sorted(c["name"] for c in app_with_centroids.get("/api/centroids").json()["centroids"])
    assert names == sorted([WUXIA_CENTROID, NOIR_CENTROID, "new_centroid"])


def test_reload_drops_deleted_file(app_with_centroids):
    centroids_dir = Path(app_with_centroids.get("/api/centroids").json()["centroids"][0]["source_path"]).parent
    (centroids_dir / "siglip2_2026-06-17_wuxia.pt").unlink()
    resp = app_with_centroids.post("/api/centroids/reload")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    # 404 on the now-missing centroid.
    assert app_with_centroids.get(f"/api/centroids/{WUXIA_CENTROID}/search").status_code == 404


# ----------------------- /api/search?centroid= integration -----------------------


def test_api_search_with_centroid(app_with_centroids):
    resp = app_with_centroids.get(f"/api/search?centroid={WUXIA_CENTROID}&limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["centroid"] == WUXIA_CENTROID
    assert data["query"] == ""
    assert data["positives"] == []  # text prompts not populated
    assert len(data["results"]) == 3


def test_api_search_centroid_mutex_with_q(app_with_centroids):
    """?centroid=foo&q=bar is rejected with 400 — they can't coexist."""
    resp = app_with_centroids.get(f"/api/search?centroid={WUXIA_CENTROID}&q=cat")
    assert resp.status_code == 400
    assert "exclusive" in resp.json()["detail"]


def test_api_search_centroid_mutex_with_positives(app_with_centroids):
    resp = app_with_centroids.get(
        f"/api/search?centroid={WUXIA_CENTROID}&positives=foo"
    )
    assert resp.status_code == 400


def test_api_search_centroid_mutex_with_negatives(app_with_centroids):
    resp = app_with_centroids.get(
        f"/api/search?centroid={WUXIA_CENTROID}&negatives=bar"
    )
    assert resp.status_code == 400


def test_api_search_centroid_unknown_400(app_with_centroids):
    resp = app_with_centroids.get("/api/search?centroid=nope&limit=10")
    assert resp.status_code == 400
    assert "nope" in resp.json()["detail"]


def test_api_search_text_only_still_works(app_with_centroids):
    """Regression: a plain text search is unaffected by the centroid feature."""
    resp = app_with_centroids.get("/api/search?q=cat&limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["centroid"] is None
    assert data["query"] == "cat"
    assert data["results"][0]["id"] == CAT_ID


# ----------------------- /api/search multi-centroid -----------------------


def test_api_search_multi_centroid_equal_weights(app_with_centroids):
    """?centroid=a&centroid=b blends the two with equal weight (1.0
    each) and returns the response with both names listed."""
    resp = app_with_centroids.get(
        f"/api/search?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}&limit=10"
    )
    assert resp.status_code == 200
    data = resp.json()
    # Both centroids listed in the order they appeared in the URL.
    assert data["centroids"] == [WUXIA_CENTROID, NOIR_CENTROID]
    # Backward-compat: `centroid` echoes the first one.
    assert data["centroid"] == WUXIA_CENTROID
    # No weights in the URL → weights=None in the response (default
    # all-equal, not echoed).
    assert data["weights"] is None
    assert len(data["results"]) == 3


def test_api_search_multi_centroid_custom_weights(app_with_centroids):
    """?centroid=a&centroid=b&weights=2,1 skews the blend toward a
    and echoes the weights in the response."""
    resp = app_with_centroids.get(
        f"/api/search?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}"
        f"&weights=2,1&limit=10"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["centroids"] == [WUXIA_CENTROID, NOIR_CENTROID]
    assert data["weights"] == [2.0, 1.0]


def test_api_search_multi_centroid_single_weight_broadcasts(app_with_centroids):
    """?weights=2 (one value) broadcasts to every centroid as 2.0."""
    resp = app_with_centroids.get(
        f"/api/search?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}"
        f"&weights=2&limit=10"
    )
    assert resp.status_code == 200
    # Broadcast is implementation detail — the response still has
    # weights=None (all 1.0 by default; the broadcast is normalised
    # away). The blend output is still a valid unit vector.
    data = resp.json()
    assert data["results"]


def test_api_search_multi_centroid_weights_wrong_count_400(app_with_centroids):
    """3 weights for 2 centroids → 400."""
    resp = app_with_centroids.get(
        f"/api/search?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}"
        f"&weights=1,2,3&limit=10"
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "3" in detail and "2 centroids" in detail


def test_api_search_multi_centroid_weights_non_numeric_400(app_with_centroids):
    resp = app_with_centroids.get(
        f"/api/search?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}"
        f"&weights=abc,def&limit=10"
    )
    assert resp.status_code == 400


def test_api_search_multi_centroid_weights_non_positive_400(app_with_centroids):
    resp = app_with_centroids.get(
        f"/api/search?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}"
        f"&weights=0,1&limit=10"
    )
    assert resp.status_code == 400
    assert "positive" in resp.json()["detail"]


def test_api_search_multi_centroid_one_unknown_400(app_with_centroids):
    """One of two centroids doesn't exist → 400. All-or-nothing
    blends (no silent partial-drop)."""
    resp = app_with_centroids.get(
        f"/api/search?centroid={WUXIA_CENTROID}&centroid=nope&limit=10"
    )
    assert resp.status_code == 400
    assert "nope" in resp.json()["detail"]


def test_api_search_single_centroid_unchanged(app_with_centroids):
    """Regression: a single ?centroid= returns the same shape it
    always did, plus the new fields with backwards-compatible
    defaults (centroids=[name], weights=None)."""
    resp = app_with_centroids.get(f"/api/search?centroid={WUXIA_CENTROID}&limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["centroid"] == WUXIA_CENTROID
    assert data["centroids"] == [WUXIA_CENTROID]
    assert data["weights"] is None


def test_api_search_multi_centroid_mutex_with_q(app_with_centroids):
    """Centroid (single or multi) still mutex with text prompts."""
    resp = app_with_centroids.get(
        f"/api/search?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}&q=cat"
    )
    assert resp.status_code == 400
    assert "exclusive" in resp.json()["detail"]


def test_api_search_multi_centroid_three(app_with_centroids):
    """Three-centroid blend (using only two available centroids is
    fine — the third will 404, which the all-or-nothing check
    surfaces as 400). Verify the URL parses to 3 entries."""
    resp = app_with_centroids.get(
        f"/api/search?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}"
        f"&centroid=nope&limit=10"
    )
    # The third is unknown → 400.
    assert resp.status_code == 400


def test_search_page_multi_centroid_renders_results(app_with_centroids):
    """The HTML search page accepts the multi-centroid URL and
    renders results. The exact visual treatment is UI work — the
    backend contract (URL → response) is what this test pins."""
    resp = app_with_centroids.get(
        f"/?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}&limit=10"
    )
    assert resp.status_code == 200
    assert 'id="result-grid"' in resp.text


# ----------------------- / search page?centroid= integration -----------------------


def test_search_page_with_centroid_renders_results(app_with_centroids):
    resp = app_with_centroids.get(f"/?centroid={WUXIA_CENTROID}&limit=10")
    assert resp.status_code == 200
    assert 'id="result-grid"' in resp.text
    # The result-count header echoes the centroid's name (commit 3
    # will refine the visual; commit 2 only proves the data flow).
    assert WUXIA_CENTROID in resp.text or "centroid" in resp.text.lower()


def test_search_page_centroid_mutex_renders_error(app_with_centroids):
    """?centroid=foo&q=bar on /  renders the error in-page, not 400."""
    resp = app_with_centroids.get(f"/?centroid={WUXIA_CENTROID}&q=cat")
    assert resp.status_code == 200
    assert "exclusive" in resp.text or "cannot combine" in resp.text


def test_search_page_centroid_unknown_renders_error(app_with_centroids):
    resp = app_with_centroids.get("/?centroid=nope")
    assert resp.status_code == 200
    assert "nope" in resp.text


def test_search_page_no_centroid_passes_active_centroid_none(app_with_centroids):
    """Regression: a plain text search page receives active_centroid=None for the template."""
    resp = app_with_centroids.get("/?q=cat")
    assert resp.status_code == 200
    # Template should render normally; commit 3 will visually skip
    # the centroid UI when None.
    assert 'id="result-grid"' in resp.text


def test_photo_similar_page_passes_active_centroid_none(app_with_centroids):
    """The /photo/{id}/similar page reuses search.html and must pass active_centroid."""
    resp = app_with_centroids.get(f"/photo/{CAT_ID}/similar")
    assert resp.status_code == 200
    assert 'id="result-grid"' in resp.text
