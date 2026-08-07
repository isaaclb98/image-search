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
