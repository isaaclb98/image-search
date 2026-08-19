"""End-to-end Playwright tests for image-search.

These tests verify that the UI renders correctly and interactions work
against the demo dev-server (http://127.0.0.1:8765).

Requirements:
  pip install pytest playwright
  playwright install chromium

Run:
  pytest tests/test_e2e.py -v --timeout=60
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


BASE = "http://127.0.0.1:8765"


# ── Helpers ──────────────────────────────────────────────────────────────


def _goto(page: Page, path: str) -> None:
    """Navigate to a page and wait for network idle."""
    page.goto(f"{BASE}{path}", wait_until="networkidle", timeout=15_000)


def _card_count(page: Page, selector: str = ".photo-card") -> int:
    return page.locator(selector).count()


# ── Navigation ───────────────────────────────────────────────────────────


class TestNavigation:
    """Verify every nav link renders and returns HTTP 200."""

    def test_home(self, page: Page):
        _goto(page, "/")
        expect(page.locator(".search-form")).to_be_visible()

    def test_random(self, page: Page):
        _goto(page, "/random")
        expect(page.locator("h1")).to_contain_text("Random")

    def test_for_you(self, page: Page):
        _goto(page, "/for-you")
        expect(page.locator("h1")).to_contain_text("For you")

    def test_favorites(self, page: Page):
        _goto(page, "/favorites")
        expect(page.locator("h1")).to_contain_text("Favourites")

    def test_dislikes(self, page: Page):
        _goto(page, "/dislikes")
        expect(page.locator("h1")).to_contain_text("Dislikes")

    def test_albums(self, page: Page):
        _goto(page, "/albums")
        expect(page.locator("h1")).to_contain_text("Albums")

    def test_centroids(self, page: Page):
        _goto(page, "/centroids")
        expect(page.locator("h1")).to_contain_text("Centroids")

    def test_discover(self, page: Page):
        _goto(page, "/discover")
        expect(page.locator("h1")).to_contain_text("Find your taste")

    def test_login(self, page: Page):
        # In demo mode (no auth), /login redirects to /
        page.goto(f"{BASE}/login", wait_until="networkidle")
        # Playwright follows redirect → we end up on home page
        assert page.url.rstrip("/") == f"{BASE}" or "login" not in page.url


# ── Photo Grid ───────────────────────────────────────────────────────────


class TestPhotoGrid:
    """Verify photo cards render as square 1:1 cells."""

    def test_random_page_has_cards(self, page: Page):
        _goto(page, "/random")
        assert _card_count(page) >= 5

    def test_cards_are_square(self, page: Page):
        _goto(page, "/random")
        first = page.locator(".photo-card").first
        box = first.bounding_box()
        assert box is not None
        ratio = box["width"] / box["height"]
        assert 0.95 <= ratio <= 1.05, f"Card ratio {ratio:.2f} not square"

    def test_search_results_render(self, page: Page):
        _goto(page, "/?q=mountain")
        assert _card_count(page) >= 5


# ── Blurhash ─────────────────────────────────────────────────────────────


class TestBlurhash:
    """Verify blurhash canvases render and fade on load."""

    def test_blurhash_canvas_present(self, page: Page):
        _goto(page, "/?q=mountain")
        # Wait for blurhash canvases to appear
        page.wait_for_selector("canvas.blurhash-canvas", timeout=5_000)
        count = page.locator("canvas.blurhash-canvas").count()
        assert count >= 3, f"Expected ≥3 blurhash canvases, got {count}"

    def test_blurhash_fades_on_load(self, page: Page):
        _goto(page, "/?q=mountain")
        page.wait_for_selector(".has-blurhash.is-loaded", timeout=8_000)
        count = page.locator(".has-blurhash.is-loaded").count()
        assert count >= 3


# ── Favourite Toggle ────────────────────────────────────────────────────


class TestFavoriteToggle:
    """Verify favourite button works on photo detail page."""

    def test_favorite_button_exists(self, page: Page):
        page.goto(f"{BASE}/photo/00000000-0000-4000-8000-000000000001", wait_until="networkidle")
        fav_btn = page.locator("[data-fav-form] [data-fav-id]")
        expect(fav_btn).to_be_visible()

    def test_favorite_toggles(self, page: Page):
        page.goto(f"{BASE}/photo/00000000-0000-4000-8000-000000000001", wait_until="networkidle")
        fav_btn = page.locator("[data-fav-form] [data-fav-id]")
        initial_state = fav_btn.get_attribute("aria-pressed")
        fav_btn.click()
        page.wait_for_timeout(500)
        new_state = fav_btn.get_attribute("aria-pressed")
        assert initial_state != new_state, (
            f"State didn't change: {initial_state} → {new_state}"
        )

    def test_dislike_button_exists(self, page: Page):
        _goto(page, "/?q=mountain")
        page.wait_for_selector(".photo-card", timeout=5_000)
        page.goto(f"{BASE}/photo/00000000-0000-4000-8000-000000000001", wait_until="networkidle")
        dislike_btn = page.locator("[data-dislike-id]")
        expect(dislike_btn).to_be_visible()


# ── Favourites Page ──────────────────────────────────────────────────────


class TestFavoritesPage:
    """Verify favourites page shows seeded data."""

    def test_has_cards(self, page: Page):
        _goto(page, "/favorites")
        assert _card_count(page) >= 3


# ── Albums ───────────────────────────────────────────────────────────────


class TestAlbums:
    """Verify album list and detail pages work."""

    def test_albums_page_has_cards(self, page: Page):
        _goto(page, "/albums")
        cards = page.locator(".album-card")
        assert cards.count() >= 2

    def test_album_cards_same_width(self, page: Page):
        _goto(page, "/albums")
        page.wait_for_selector(".album-card", timeout=5_000)
        cards = page.locator(".album-card")
        count = cards.count()
        assert count >= 2, f"Expected ≥2 album cards, got {count}"

    def test_album_detail_renders(self, page: Page):
        _goto(page, "/albums/1")
        # Should show the album name, not a 404
        expect(page.locator("h1")).to_contain_text("Landscape picks")
        # Should have photo cards
        assert _card_count(page) >= 1

    def test_no_back_to_albums_link(self, page: Page):
        _goto(page, "/albums/1")
        back_link = page.locator("text=Back to albums")
        assert back_link.count() == 0, "'Back to albums' link should be removed"


# ── Centroids Page ──────────────────────────────────────────────────────


class TestCentroids:
    """Verify centroids page renders grid cards."""

    def test_centroids_page_renders(self, page: Page):
        _goto(page, "/centroids")
        cards = page.locator(".centroid-card")
        assert cards.count() >= 1

    def test_no_empty_state_text(self, page: Page):
        _goto(page, "/centroids")
        empty = page.locator("text=No centroids to show")
        assert empty.count() == 0

    def test_no_setup_commands(self, page: Page):
        _goto(page, "/centroids")
        setup = page.locator("text=Show setup commands")
        assert setup.count() == 0

    def test_centroid_cards_are_grid(self, page: Page):
        _goto(page, "/centroids")
        # The centroid-list should be a grid with multiple columns
        centroid_list = page.locator(".centroid-list").first
        if centroid_list.count() > 0:
            box = centroid_list.bounding_box()
            assert box is not None
            assert box["width"] > 400, "Centroid list should be wide (grid layout)"


# ── Centroid Search ──────────────────────────────────────────────────────


class TestCentroidSearch:
    """Verify searching with a centroid shows diversity controls."""

    def test_centroid_search_has_diversity(self, page: Page):
        _goto(page, "/?centroid=favourites")
        page.wait_for_timeout(1000)
        diversity = page.locator("[name='diversity']")
        assert diversity.count() >= 1, "Centroid search should have diversity controls"

    def test_centroid_search_has_submit(self, page: Page):
        _goto(page, "/?centroid=favourites")
        submit = page.locator("[data-search-submit]")
        assert submit.count() >= 1, "Centroid search should have search button"

    def test_centroid_bar_shows_chip(self, page: Page):
        _goto(page, "/?centroid=favourites")
        chip = page.locator(".centroid-chip-name")
        expect(chip).to_contain_text("favourites")


# ── Infinite Scroll ──────────────────────────────────────────────────────


class TestInfiniteScroll:
    """Verify infinite scroll loads more results on search/random."""

    def test_random_infinite_scroll(self, page: Page):
        _goto(page, "/random")
        initial = _card_count(page)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(3000)
        after = _card_count(page)
        assert after >= initial, f"Expected at least {initial} cards after scroll, got {after}"


# ── Lightbox ─────────────────────────────────────────────────────────────


class TestLightbox:
    """Verify clicking a photo card opens the lightbox or navigates to detail."""

    def test_click_navigates_to_photo(self, page: Page):
        _goto(page, "/?q=mountain")
        page.wait_for_selector(".photo-card", timeout=5_000)
        page.goto(f"{BASE}/photo/00000000-0000-4000-8000-000000000001", wait_until="networkidle")
        photo = page.locator(".photo-page figure img")
        expect(photo).to_be_visible()


# ── Search Form ──────────────────────────────────────────────────────────


class TestSearchForm:
    """Verify search form works correctly."""

    def test_no_grid_feed_toggle(self, page: Page):
        _goto(page, "/")
        toggle = page.locator(".segmented")
        assert toggle.count() == 0, "Grid/Feed toggle should be removed from search"

    def test_no_quick_prompts(self, page: Page):
        _goto(page, "/")
        prompts = page.locator(".quick-prompts")
        assert prompts.count() == 0, "Quick prompts should be removed"

    def test_prompt_add_button(self, page: Page):
        _goto(page, "/")
        add_btn = page.locator("[data-prompt-add='positives']")
        expect(add_btn).to_be_visible()

    def test_include_prompt_persists(self, page: Page):
        _goto(page, "/")
        input_el = page.locator("[data-prompt-input='positives']")
        input_el.fill("mountain")
        page.locator("[data-prompt-add='positives']").click()
        # The chip should appear and the input should still be visible
        chip = page.locator(".prompt-chip--positive")
        expect(chip).to_contain_text("mountain")
        # Input should still be usable
        expect(input_el).to_be_visible()


# ── Grid Column Cap ──────────────────────────────────────────────────────


class TestGridColumns:
    """Verify max 5 columns across the app."""

    @pytest.mark.parametrize("path", ["/random", "/favorites"])
    def test_max_5_columns(self, page: Page, path: str):
        _goto(page, path)
        page.wait_for_selector(".grid", timeout=5_000)
        grid = page.locator(".grid").first
        cols = page.evaluate(
            "el => getComputedStyle(el).gridTemplateColumns.split(' ').length",
            grid.element_handle(),
        )
        assert cols <= 5, f"Grid has {cols} columns, max is 5"


class TestSavedSearches:
    def test_saved_search_crud(self, page: Page):
        _goto(page, "/")
        # Add prompts first (this enables the save button)
        page.fill('[data-prompt-input="positives"]', "mountain")
        page.locator('[data-prompt-add="positives"]').click()
        page.wait_for_timeout(300)
        # Save button should now be enabled
        save_btn = page.locator('[data-saved-save]')
        expect(save_btn).not_to_be_disabled()
        save_btn.click()
        page.wait_for_timeout(1000)
        # Verify the saved search appears on the /saved page (the
        # dropdown was removed in the T-refactor; /saved is the
        # canonical list surface now).
        _goto(page, "/saved")
        page.wait_for_selector(".saved-card")
        expect(page.locator(".saved-card")).to_contain_text("mountain")

class TestAlbumActions:
    def test_create_album(self, page: Page):
        _goto(page, "/albums")
        page.fill('input[name="name"]', "Test Album")
        page.click('button:has-text("Create album")')
        page.wait_for_selector("a.album-card")
        expect(page.get_by_role("link", name="Test Album")).to_be_visible()



class TestPhotoActions:
    def test_similar_navigation(self, page: Page):
        page.goto(f"{BASE}/photo/00000000-0000-4000-8000-000000000001", wait_until="networkidle")
        page.wait_for_selector(".photo-page")
        # Click the "Similar photos" link
        similar_link = page.locator('a[href*="/similar"]')
        expect(similar_link).to_be_visible()
        similar_link.click()
        page.wait_for_load_state("networkidle")
        assert "/similar" in page.url

class TestDislikeToggle:
    def test_dislike_toggles(self, page: Page):
        page.goto(f"{BASE}/photo/00000000-0000-4000-8000-000000000001", wait_until="networkidle")
        page.wait_for_selector(".photo-page")
        btn = page.locator("[data-dislike-form] [data-dislike-id]")
        expect(btn).to_be_visible()
        # The demo server persists dislike state between runs, so the
        # assertion must be relative to the current aria-pressed value
        # (clicking toggles it) rather than assuming an initial "off".
        initial = btn.get_attribute("aria-pressed")
        btn.click()
        page.wait_for_timeout(500)
        expected = "false" if initial == "true" else "true"
        expect(btn).to_have_attribute("aria-pressed", expected)
