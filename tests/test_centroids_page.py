"""
tests/test_centroids_page.py

Layer 2 — page-render tests for the centroids list page, the
nav link, and the search-page centroid-mode indicator.

Reuses the app_with_centroids fixture from test_centroids_api.py
(two valid centroids, one mismatched file skipped).
"""

from __future__ import annotations

from _centroid_fixture import NOIR_CENTROID, WUXIA_CENTROID
from fastapi.testclient import TestClient

# ----------------------- /centroids HTML page -----------------------


def test_centroids_page_renders_loaded(app_with_centroids):
    """The list page shows one card per loaded centroid."""
    resp = app_with_centroids.get("/centroids")
    assert resp.status_code == 200
    body = resp.text
    # The two valid centroids show up as cards.
    assert WUXIA_CENTROID in body
    assert NOIR_CENTROID in body
    # Each card has a 'search with this centroid' link to /?centroid=...
    assert f'/?centroid={WUXIA_CENTROID}' in body
    assert f'/?centroid={NOIR_CENTROID}' in body
    # Model/dim metadata is shown.
    assert "siglip2" in body
    assert "1536" in body
    # The skipped dinov3 file is NOT in the rendered list.
    assert "dinov3_junk" not in body


def test_centroids_page_shows_source_dir(app_with_centroids):
    """The page exposes the CENTROIDS_DIR path so the user knows where it scanned."""
    resp = app_with_centroids.get("/centroids")
    assert resp.status_code == 200
    assert "centroids" in resp.text  # path component


def test_centroids_page_has_reload_button(app_with_centroids):
    """Reload button is rendered when centroids_dir is set."""
    resp = app_with_centroids.get("/centroids")
    assert resp.status_code == 200
    assert 'data-centroids-reload' in resp.text


def test_centroids_page_loads_centroids_js(app_with_centroids):
    """The reload JS module is included (when centroids_dir is set)."""
    resp = app_with_centroids.get("/centroids")
    assert resp.status_code == 200
    assert "centroids" in resp.text


def test_centroids_page_empty_state_when_no_dir():
    """When CENTROIDS_DIR is unset, the page shows the empty-state hint."""
    from qdrant_client import QdrantClient

    from indexer import upsert
    from indexer.upsert import VECTOR_DIM
    from search import app as app_mod
    from search.config import Config
    from search.qdrant_client import QdrantSearch

    client = QdrantClient(location=":memory:")
    qdrant = QdrantSearch(client=client, collection="images_test_empty_page", timeout_ms=2000)
    upsert.ensure_collection(client, qdrant.collection, dim=VECTOR_DIM)

    cfg = Config(
        qdrant_url="memory://",
        qdrant_collection=qdrant.collection,
        qdrant_api_key=None,
        model_name="mock", model_revision="", device="cpu",
        top_k_default=50, top_k_max=200, query_timeout_ms=2000,
        nas_images_base="/tmp", path_prefix="", web_ui_url="http://localhost:8000",  # noqa: S108 - test fixture
        log_level="WARNING", test_mode=True,
        centroids_dir=None,
    )
    app_mod.reset_for_tests()
    app = app_mod.create_app(cfg=cfg, qdrant=qdrant)
    with TestClient(app) as tc:
        resp = tc.get("/centroids")
        assert resp.status_code == 200
        # No static cards rendered (no centroids_dir configured).
        # The dynamic section IS rendered — even with zero favourites,
        # the favourites centroid shows up with its empty-state hint.
        assert "Favourites" in resp.text
        assert "Favourite a few photos first" in resp.text
        # No reload button when no directory is configured.
        assert 'data-centroids-reload' not in resp.text
    app_mod.reset_for_tests()


# ----------------------- nav link -----------------------


def test_centroids_nav_link_on_home_page(app_with_centroids):
    """The site header has a 'Centroids' link in the nav."""
    resp = app_with_centroids.get("/")
    assert resp.status_code == 200
    assert 'href="/centroids"' in resp.text
    assert ">Centroids<" in resp.text


def test_centroids_nav_link_on_discover_page(app_with_centroids):
    """The nav link is on every page (base template)."""
    resp = app_with_centroids.get("/discover")
    assert resp.status_code == 200
    assert 'href="/centroids"' in resp.text


# ----------------------- search page indicator -----------------------


def test_search_page_shows_centroid_indicator(app_with_centroids):
    """When ?centroid=foo is active, the indicator block is rendered with the name."""
    resp = app_with_centroids.get(f"/?centroid={WUXIA_CENTROID}")
    assert resp.status_code == 200
    assert "centroid-bar" in resp.text
    assert WUXIA_CENTROID in resp.text
    # The 'use text search' switch link is present.
    assert "use text search" in resp.text


def test_search_page_hides_prompt_composition_in_centroid_mode(app_with_centroids):
    """Prompt composition rows are hidden when centroid is active (mutex honesty)."""
    resp = app_with_centroids.get(f"/?centroid={WUXIA_CENTROID}")
    assert resp.status_code == 200
    # The centroid bar IS rendered (so users know they're in centroid mode).
    assert "centroid-bar" in resp.text


def test_search_page_keeps_search_enabled_in_centroid_mode(app_with_centroids):
    """The prompt composer is hidden, but Search remains available so
    centroid users can apply Diversity and other search controls."""
    resp = app_with_centroids.get(f"/?centroid={WUXIA_CENTROID}")
    assert resp.status_code == 200
    # The Search submit button is present (so diversity can be re-applied).
    assert 'data-search-submit' in resp.text
    assert "class=\"search-submit\"" in resp.text


def test_search_page_centroid_diversity_control_is_selected(app_with_centroids):
    """Centroid searches expose the active Diversity mode for resubmission."""
    resp = app_with_centroids.get(
        f"/?centroid={WUXIA_CENTROID}&diversity=balanced&diversity_depth=2000"
    )
    assert resp.status_code == 200
    # The diversity-depth numeric input is pre-populated with the value.
    assert 'value="2000" selected' in resp.text
    assert "class=\"search-submit\"" in resp.text


def test_search_page_no_indicator_without_centroid(app_with_centroids):
    """A plain text search does not show the centroid indicator."""
    resp = app_with_centroids.get("/?q=cat")
    assert resp.status_code == 200
    assert "centroid-bar" not in resp.text
    # Prompt composition is visible as usual.


# ----------------------- multi-centroid UI -----------------------


def test_search_page_single_centroid_chip(app_with_centroids):
    """Single centroid: one chip, with the centroid name and a ×
    that links to the URL with that centroid stripped."""
    resp = app_with_centroids.get(f"/?centroid={WUXIA_CENTROID}")
    assert resp.status_code == 200
    # Chip stack present with one item.
    assert "centroid-chip-list" in resp.text
    assert "centroid-chip glass--sharp" in resp.text
    assert WUXIA_CENTROID in resp.text
    # The × remove link goes to / (no centroid params left).
    assert 'href="/"' in resp.text


def test_search_page_multi_centroid_chip_stack(app_with_centroids):
    """Two centroids: chip stack renders both, each × links to the
    URL with that centroid stripped (the other preserved)."""
    resp = app_with_centroids.get(
        f"/?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}"
    )
    assert resp.status_code == 200
    # Both centroids listed in the chip stack.
    assert WUXIA_CENTROID in resp.text
    assert NOIR_CENTROID in resp.text
    # The header reflects the multi-centroid state.
    assert "Blending 2 centroids" in resp.text
    # The × for the first chip links to /?centroid=<the other one>.
    other_url = f"/?centroid={NOIR_CENTROID}"
    assert other_url in resp.text
    # And vice versa.
    first_url = f"/?centroid={WUXIA_CENTROID}"
    assert first_url in resp.text


def test_search_page_result_count_blend_label(app_with_centroids):
    """The result-count header reads 'Blending centroid: "X"' for a
    single centroid, and 'Blending N centroids' for multiple."""
    resp_single = app_with_centroids.get(f"/?centroid={WUXIA_CENTROID}")
    body_single = resp_single.text
    # The single-centroid bar shows the centroid name in the label.
    assert "Blending centroid:" in body_single
    assert WUXIA_CENTROID in body_single
    # "centroid" (singular) appears, "centroids" (plural) does not
    # in the chip bar — the chip list contains each name.
    assert "Blending 2 centroids" not in body_single

    resp_multi = app_with_centroids.get(
        f"/?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}"
    )
    body_multi = resp_multi.text
    # The multi-centroid bar shows the count and the chip list has both.
    assert "Blending 2 centroids" in body_multi
    assert WUXIA_CENTROID in body_multi
    assert NOIR_CENTROID in body_multi


def test_search_page_chip_remove_url_preserves_other_params(app_with_centroids):
    """When removing a centroid chip ×, all other URL params
    (positives, view, weights, etc.) must round-trip. Strip one
    centroid and keep everything else."""
    resp = app_with_centroids.get(
        f"/?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}"
        f"&view=feed&weights=2,1"
    )
    assert resp.status_code == 200
    body = resp.text
    # × for WUXIA strips WUXIA but keeps NOIR + view + weights.
    expected = f"/?centroid={NOIR_CENTROID}&amp;view=feed&amp;weights=2%2C1"
    assert expected in body
    # × for NOIR strips NOIR but keeps WUXIA + view + weights.
    expected_other = f"/?centroid={WUXIA_CENTROID}&amp;view=feed&amp;weights=2%2C1"
    assert expected_other in body


def test_search_page_centroid_link_text_single(app_with_centroids):
    """Single centroid shows the chip with the centroid name and
    the 'use text search instead' switch link."""
    resp = app_with_centroids.get(f"/?centroid={WUXIA_CENTROID}")
    assert resp.status_code == 200
    assert WUXIA_CENTROID in resp.text
    assert "use text search" in resp.text


def test_search_page_centroid_link_text_multi(app_with_centroids):
    """Multi-centroid shows 'Blending N centroids' (plural label)."""
    resp = app_with_centroids.get(
        f"/?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}"
    )
    assert "Blending 2 centroids" in resp.text


def test_centroids_page_renders_checkboxes(app_with_centroids):
    """Every loadable centroid card has a checkbox."""
    resp = app_with_centroids.get("/centroids")
    assert resp.status_code == 200
    # Two valid centroids => two checkboxes with the right names.
    assert 'data-centroid-checkbox="wuxia_female_leads"' in resp.text
    assert 'data-centroid-checkbox="noir_cinematography"' in resp.text
    # Selection bar is rendered (but hidden by default — JS toggles).
    assert 'data-centroid-selection-bar' in resp.text
    assert 'data-selection-cta' in resp.text


def test_centroids_page_prechecks_preselected_centroids(app_with_centroids):
    """When the URL has ?centroid=a&centroid=b, the matching
    checkboxes are rendered as `checked` so the user can land on
    /centroids from a blended search and add a third centroid
    without re-picking the first two."""
    resp = app_with_centroids.get(
        f"/centroids?centroid={WUXIA_CENTROID}&centroid={NOIR_CENTROID}"
    )
    assert resp.status_code == 200
    body = resp.text
    # Both centroids' checkboxes are checked.
    # Use a regex-free substring check: the checkbox tag precedes
    # its data attribute, and we want to find 'data-centroid-checkbox="X"'
    # with a `checked` keyword before the closing >.
    # Check for the specific checked pattern.
    for c in (WUXIA_CENTROID, NOIR_CENTROID):
        pattern = (
            f'data-centroid-checkbox="{c}"'
        )
        idx = body.find(pattern)
        assert idx != -1, f"checkbox for {c} not found"
        # Walk back to the opening `<input` and forward to `>` —
        # verify `checked` appears in that span.
        open_idx = body.rfind("<input", 0, idx)
        close_idx = body.find(">", idx)
        span = body[open_idx:close_idx + 1]
        assert "checked" in span, (
            f"checkbox for {c} should be pre-checked: {span}"
        )

    # The other one (dinov3_junk) is NOT in the rendered list, so it
    # can't be prechecked — guard against an off-by-one where the
    # page thinks it should precheck something it doesn't render.
    assert "dinov3_junk" not in body


def test_centroids_page_uncheckbox_when_no_preselection(app_with_centroids):
    """When the URL has no centroid params, no checkbox is checked
    by default. Sanity check against accidentally prechecking all."""
    resp = app_with_centroids.get("/centroids")
    body = resp.text
    # No `checked` should appear on any centroid checkbox span.
    # Use a targeted search.
    for c in (WUXIA_CENTROID, NOIR_CENTROID):
        pattern = f'data-centroid-checkbox="{c}"'
        idx = body.find(pattern)
        open_idx = body.rfind("<input", 0, idx)
        close_idx = body.find(">", idx)
        span = body[open_idx:close_idx + 1]
        assert "checked" not in span
