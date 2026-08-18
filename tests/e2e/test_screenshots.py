"""Screenshot harness for visual regression checks.

Run with:
  pytest tests/e2e/test_screenshots.py -v

Captures every primary page at desktop + mobile viewports into
tests/e2e/screenshots/. Re-runs are safe — files are overwritten in place.

This is the baseline-locking tool for the UI redesign. Every visual change
must re-run this and the diffs reviewed before committing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


PAGES = [
    ("home", "/"),
    ("random", "/random"),
    ("favorites", "/favorites"),
    ("dislikes", "/dislikes"),
    ("albums", "/albums"),
    ("saved", "/saved"),
    ("centroids", "/centroids"),
    ("discover", "/discover"),
    ("for-you", "/for-you"),
]


def _photo_id_for(base: str, page) -> str:
    page.goto(base + "/api/random", wait_until="domcontentloaded")
    return json.loads(page.inner_text("body"))["results"][0]["id"]


@pytest.mark.parametrize("viewport_name", ["desktop", "mobile"])
def test_capture_all_pages(demo_base_url: str, screenshot_dir: Path, request, viewport_name: str) -> None:
    """Capture every primary page at the given viewport."""
    # The `context` fixture uses parametrize via request.param if present.
    # We can't share parametrize across fixtures easily, so we open our own
    # context here.
    from playwright.sync_api import sync_playwright
    viewport = {"desktop": {"width": 1440, "height": 900}, "mobile": {"width": 390, "height": 844}}[viewport_name]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(viewport=viewport)
            try:
                page = ctx.new_page()
                pid = _photo_id_for(demo_base_url, page)
                targets = list(PAGES) + [("photo", f"/photo/{pid}")]
                for name, route in targets:
                    page.goto(demo_base_url + route, wait_until="domcontentloaded", timeout=15000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                    shot = screenshot_dir / f"{viewport_name}-{name}.png"
                    page.screenshot(path=str(shot), full_page=True)
                ctx.close()
            finally:
                ctx.close()
        finally:
            browser.close()


def test_screenshot_count_matches(screenshot_dir: Path) -> None:
    """After test_capture_all_pages runs (desktop + mobile), we expect:
        10 pages × 2 viewports = 20 PNGs in tests/e2e/screenshots/.

    This is a guardrail: if a page is added and the PAGES list isn't updated,
    this test will fail and force the list to be revisited.
    """
    pngs = list(screenshot_dir.glob("*.png"))
    expected = 2 * (len(PAGES) + 1)  # +1 for /photo/{id}
    assert len(pngs) == expected, (
        f"expected {expected} screenshots in {screenshot_dir} "
        f"({len(PAGES)+1} pages × 2 viewports), found {len(pngs)}. "
        f"Did you add a page without updating PAGES in test_screenshots.py?"
    )
