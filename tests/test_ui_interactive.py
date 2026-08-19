"""Deterministic source guards for the interactive UI layer.

Browser behavior is smoke-tested separately with Playwright; these checks keep
the server-rendered contracts and shipped assets from drifting silently.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "search" / "templates"
STATIC = ROOT / "search" / "static"


def read(path: Path) -> str:
    return path.read_text()


def test_theme_bridge_covers_design_tokens_and_mobile_layout():
    # Theme tokens live in tokens.css; the responsive search-form
    # layout lives in layout.css. Combined they cover the contract
    # the old input.css (DaisyUI theme bridge) used to hold.
    tokens = read(STATIC / "css" / "tokens.css")
    layout = read(STATIC / "css" / "layout.css")
    assert "--bg-base" in tokens
    assert "--fg" in tokens
    assert "--accent-500" in tokens
    assert "--border-glass" in tokens
    assert "@media (max-width: 760px)" in layout
    assert ".search-form" in layout


def test_base_exposes_mobile_navigation_viewer_and_shortcuts():
    base = read(TEMPLATES / "base.html")
    # The old slide-in "mobile-nav" drawer was replaced by a single
    # responsive pill row (navbar-center / nav-list) that works at
    # every viewport.
    assert "navbar-center" in base
    assert "nav-list" in base
    assert "data-photo-lightbox" in base
    assert "data-lightbox-albums" in base
    assert "data-lightbox-prev" in base
    assert "data-lightbox-next" in base
    assert "data-shortcuts-dialog" in base
    assert "{{ asset('js/ui.js') }}" in base


def test_result_grid_exposes_photo_and_favorite_contracts():
    grid = read(TEMPLATES / "_result_grid.html")
    assert "data-lightbox-trigger" in grid
    assert "data-photo-path" in grid
    assert "data-favorite-toggle" in grid
    assert "aria-pressed=" in grid
    liked = read(TEMPLATES / "discover_liked.html")
    assert "data-lightbox-trigger" in liked
    assert "data-blurhash" in liked
    assert "data-favorite-toggle" in liked
    assert "data-favorites-page" in read(TEMPLATES / "favorites.html")
    assert "favoritechanged" in read(STATIC / "js" / "favorites.js")


def test_interactive_assets_are_present_and_cross_referenced():
    blurhash = read(STATIC / "js" / "lib" / "blurhash.js")
    photo_card = read(STATIC / "js" / "lib" / "photo-card.js")
    ui = read(STATIC / "js" / "ui.js")
    grid = read(STATIC / "js" / "lib" / "grid.js")
    feed = read(STATIC / "js" / "lib" / "feed.js")
    assert "decodeBlurHash" in blurhash
    assert "enhancePhotoCards" in photo_card
    assert "data-lightbox-trigger" in ui
    assert "data-lightbox-prev" in ui
    assert "data-lightbox-next" in ui
    assert '"./photo-card.js"' in grid
    assert '"./photo-card.js"' in feed


def test_remaining_pages_use_the_shared_state_language():
    for name in ("album_detail.html", "discover.html", "discover_liked.html"):
        source = read(TEMPLATES / name)
        assert '{% import "_macros.html" as ui %}' in source
    assert "ui.empty_state(" in read(TEMPLATES / "album_detail.html")
    assert "ui.empty_state(" in read(TEMPLATES / "discover_liked.html")


def test_search_surface_uses_prompts_and_explicit_submit_gate():
    search = read(TEMPLATES / "search.html")
    controller = read(STATIC / "js" / "ui.js")
    prompts = read(STATIC / "js" / "lib" / "prompts.js")
    assert 'name="q"' not in search
    assert 'data-prompt-input="positives"' in search
    assert 'data-prompt-input="negatives"' in search
    assert 'name="positives"' in search
    assert 'name="negatives"' in search
    assert "data-search-submit" in search
    # Explicit submit gate: the form submit is intercepted and the
    # search only runs once the prompt state is composed (the old
    # search.js draft/dirty machinery was folded into ui.js).
    assert 'searchForm.addEventListener("submit"' in controller
    assert "event.preventDefault()" in controller
    assert 'params.append("positives", p)' in controller
    assert "promptschanged" in controller
    assert "class PromptChips" in prompts


def test_result_page_javascript_fallback_limits_are_35():
    ui = read(STATIC / "js" / "ui.js")
    random = read(STATIC / "js" / "random.js")
    favorites = read(STATIC / "js" / "favorites.js")

    assert 'dataset.limit || "35"' in ui
    assert "Number.isFinite(n) && n > 0 ? n : 35" in random
    assert 'grid?.dataset.limit || "35"' in favorites


def test_centroid_search_submit_preserves_centroid_and_weights():
    url = read(STATIC / "js" / "lib" / "url.js")
    ui = read(STATIC / "js" / "ui.js")

    assert "readCentroids()" in url
    assert 'params.append("centroid", centroid)' in url
    assert "readCentroidWeights()" in url
    assert 'params.set("weights", weights.join(","))' in url
    # buildSearchUrl (ui.js) preserves the filename param alongside
    # centroid + weights on the infinite-scroll fetch (search.js was
    # folded into ui.js in the T-refactor).
    assert "function buildSearchUrl()" in ui
    assert 'params.set("filename", filename)' in ui


def test_download_zip_buttons_keep_text_centered_and_inside():
    glass = read(STATIC / "css" / "glass.css")
    favorites = read(TEMPLATES / "favorites.html")
    album = read(TEMPLATES / "album_detail.html")

    # Favourites kept the bespoke download-zip-btn class; the album
    # detail page now uses the shared .btn.btn-outline layout classes.
    assert "download-zip-btn" in favorites
    assert "btn btn-outline" in album
    # The .btn primitive centers the label text inside the button.
    assert ".btn {" in glass
    for declaration in (
        "display: inline-flex",
        "align-items: center",
        "justify-content: center",
    ):
        assert declaration in glass


def test_random_page_grid_uses_shared_grid_cap():
    """The Random tab now shares the responsive grid with the rest of
    the app — the old 7-column override was dropped in the T-refactor.
    The shared grid is capped at 5 columns at wide viewports, so
    /random can never render an unbounded row of images."""
    css = read(STATIC / "css" / "layout.css")
    block_idx = css.find("@media (min-width: 1600px)")
    assert block_idx != -1, "1600px grid-cap media query not found"
    close = css.find("}", block_idx)
    block = css[block_idx:close + 1]
    assert "repeat(5," in block, (
        f"shared .grid should cap at 5 columns at 1600px+, got: {block!r}"
    )
