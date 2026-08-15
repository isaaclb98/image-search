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


def test_theme_bridge_covers_legacy_variables_and_mobile_layout():
    css = read(STATIC / "css" / "input.css")
    assert '[data-theme="light"]' in css
    assert '[data-theme="dark"]' in css
    for variable in ("--bg", "--fg", "--muted", "--accent", "--glass-border"):
        assert variable in css
    assert "@media (max-width: 760px)" in css
    assert ".search-form { display: flex; flex-direction: column" in css


def test_base_exposes_mobile_navigation_viewer_and_shortcuts():
    base = read(TEMPLATES / "base.html")
    assert "mobile-nav" in base
    assert "data-photo-lightbox" in base
    assert "data-lightbox-albums" in base
    assert "data-lightbox-prev" in base
    assert "data-lightbox-next" in base
    assert "data-shortcuts-dialog" in base
    assert '/static/js/ui.js?v={{ static_assets_version }}' in base


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
    controller = read(STATIC / "js" / "search.js")
    assert 'name="q"' not in search
    assert 'data-prompt-input="positives"' in search
    assert 'data-prompt-input="negatives"' in search
    assert 'name="positives"' in search
    assert 'name="negatives"' in search
    assert 'data-search-draft-status' in search
    assert "history.pushState({ search: url }" in controller
    assert "function markDraftDirty()" in controller
    assert "form.requestSubmit()" not in controller
    assert "if (hasPositivePrompt(readQuery())) runSearch()" not in controller
    assert "if (loadingMore || loadingSearch || !grid || draftDirty) return;" in controller
    assert "new AbortController()" in controller
    assert 'submitButton.dataset.locked = "true"' in controller
    assert "if (rawValue.trim()) promptChips.add(side, rawValue);" in controller
    assert "let searchGeneration = 0;" in controller
    assert "signal: controller.signal" in controller
    assert "requestGeneration !== searchGeneration" in controller


def test_result_page_javascript_fallback_limits_are_35():
    search = read(STATIC / "js" / "search.js")
    random = read(STATIC / "js" / "random.js")
    favorites = read(STATIC / "js" / "favorites.js")

    assert 'grid.dataset.limit || "35"' in search
    assert "Number.isFinite(n) && n > 0 ? n : 35" in random
    assert 'grid?.dataset.limit || "35"' in favorites


def test_download_zip_buttons_keep_text_centered_and_inside():
    source = read(STATIC / "css" / "input.css")
    bundle = read(STATIC / "css" / "app.css")
    favorites = read(TEMPLATES / "favorites.html")
    album = read(TEMPLATES / "album_detail.html")
    selector = ".download-zip-btn"

    assert selector.removeprefix(".") in favorites
    assert selector.removeprefix(".") in album
    assert selector in source
    for declaration in (
        "display: inline-flex",
        "align-items: center",
        "justify-content: center",
        "white-space: nowrap",
        "line-height: 1.2",
    ):
        assert declaration in source
        assert declaration in bundle

    # The custom rule must be emitted after DaisyUI's `.btn` rule so
    # the legacy import cannot turn the link back into inline content.
    assert bundle.rfind(selector) > bundle.find("    .btn {")


def test_random_page_grid_uses_seven_columns():
    """The Random tab should render exactly 7 columns of images,
    not the responsive default the search/favorites grid uses.

    The user prefers a denser layout on /random. The fix overrides
    .grid inside .random-page to force 7 columns at every viewport.
    """
    css = read(STATIC / "css" / "app.css")
    # Find the override block.
    block_idx = css.find(".random-page .grid")
    assert block_idx != -1, ".random-page .grid override not found"
    close = css.find("}", block_idx)
    block = css[block_idx:close + 1]
    assert "repeat(7," in block, (
        f".random-page .grid should force 7 columns, got: {block!r}"
    )
