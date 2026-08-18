"""Per-page E2E smoke tests for the demo server.

These are intentionally basic: every page must
  - respond 200 (or an expected redirect)
  - render without JS errors
  - render without page errors
  - complete every network request without failure

A handful of pages have route-specific assertions (h1 present, photo tile
present, etc.) to catch regressions in core layout.

These tests run against the live demo server started by the session-scoped
demo_base_url fixture. They are NOT a substitute for the existing API tests
in tests/test_*.py — those cover correctness; these cover rendering.
"""
from __future__ import annotations

from typing import Any

import pytest

# (name, route, expected_status, expected_selector_or_None)
PAGES = [
    ("home", "/", 200, "nav"),
    ("random", "/random", 200, "h1"),
    ("favorites", "/favorites", 200, "h1"),
    ("dislikes", "/dislikes", 200, "h1"),
    ("albums", "/albums", 200, "h1"),
    ("saved", "/saved", 200, "h1"),
    ("centroids", "/centroids", 200, "h1"),
    ("discover", "/discover", 200, "h1"),
    ("for-you", "/for-you", 200, "h1"),
]

# Routes with a redirect or error contract that users actually see.
# (name, route, post-redirect URL, post-redirect status, reason).
# Note: Playwright follows redirects by default, so these tests assert the
# end state the user lands on, not the redirect hop itself.
EXPECTED_NON_200 = [
    ("login-get", "/login", "/", 200, "GET /login 302-redirects to / when authenticated; user lands on home with 200"),
]


@pytest.mark.parametrize("name,route,status,selector", PAGES)
def test_page_renders(demo_base_url: str, page: Any, name: str, route: str, status: int, selector: str) -> None:
    """Every primary page returns its expected status and renders without errors."""
    resp = page.goto(demo_base_url + route, wait_until="domcontentloaded", timeout=15000)
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    assert resp is not None, f"{name}: no response"
    assert resp.status == status, f"{name}: expected {status}, got {resp.status}"
    assert page._e2e_page_errors == [], f"{name}: page errors: {page._e2e_page_errors}"
    assert page._e2e_console_errors == [], f"{name}: console errors: {page._e2e_console_errors}"
    assert page._e2e_failed_requests == [], f"{name}: failed requests: {page._e2e_failed_requests}"
    el = page.query_selector(selector)
    assert el is not None, f"{name}: expected selector {selector!r} not found in DOM"


@pytest.mark.parametrize("name,route,final_url,status,reason", EXPECTED_NON_200)
def test_known_non_200_routes(demo_base_url: str, page: Any, name: str, route: str, final_url: str, status: int, reason: str) -> None:
    """Routes that redirect or error must keep their user-facing contract.

    If this test ever fails because one of these routes starts behaving
    differently, that's a contract change — update EXPECTED_NON_200 accordingly
    rather than treating the test as a regression.
    """
    resp = page.goto(demo_base_url + route, wait_until="domcontentloaded", timeout=15000)
    assert resp is not None, f"{name}: no response"
    assert resp.status == status, f"{name} ({reason}): expected {status}, got {resp.status}"
    assert page.url.endswith(final_url), f"{name}: expected to land on {final_url}, got {page.url}"


@pytest.mark.parametrize("name,route", [("discover-liked-no-session", "/discover/liked")])
def test_routes_that_require_query_params(demo_base_url: str, page: Any, name: str, route: str) -> None:
    """/discover/liked requires ?session_id=...; missing it returns 422.

    Tracked separately because the contract is "missing required query param",
    which is a different category from "redirects to a sane place".
    """
    resp = page.goto(demo_base_url + route, wait_until="domcontentloaded", timeout=15000)
    assert resp is not None
    assert resp.status == 422, f"{name}: expected 422 (missing session_id), got {resp.status}"


def test_photo_detail_renders(demo_base_url: str, page: Any) -> None:
    """/photo/{id} uses a real id from /api/random."""
    page.goto(demo_base_url + "/api/random", wait_until="domcontentloaded")
    import json
    payload = json.loads(page.inner_text("body"))
    pid = payload["results"][0]["id"]
    resp = page.goto(f"{demo_base_url}/photo/{pid}", wait_until="domcontentloaded", timeout=15000)
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    assert resp is not None and resp.status == 200
    assert page._e2e_page_errors == [], page._e2e_page_errors
    assert page._e2e_console_errors == [], page._e2e_console_errors


def test_navigation_present_on_every_page(demo_base_url: str, context: Any) -> None:
    """Every primary page must have a <nav> element (global nav)."""
    import json
    page = context.new_page()
    try:
        # Get a real photo id first (some nav links may point to /photo/{id})
        page.goto(demo_base_url + "/api/random", wait_until="domcontentloaded")
        pid = json.loads(page.inner_text("body"))["results"][0]["id"]
        for name, route, *_ in PAGES + [("photo", f"/photo/{pid}", 200, "nav")]:
            page.goto(demo_base_url + route, wait_until="domcontentloaded", timeout=15000)
            nav = page.query_selector("nav")
            assert nav is not None, f"{name}: <nav> not found"
    finally:
        page.close()


def test_seed_populated_pages_have_content(demo_base_url: str, page: Any) -> None:
    """After seed-demo runs, /favorites, /albums, /dislikes must have rows."""
    # /favorites
    page.goto(demo_base_url + "/favorites", wait_until="domcontentloaded")
    assert page._e2e_page_errors == []
    # A populated /favorites must have at least one photo-tile-ish element.
    # We don't pin a class name — just look for an image tag inside the main grid.
    imgs = page.query_selector_all("main img, [class*='grid'] img, [class*='result'] img")
    assert len(imgs) >= 1, "favorites page rendered without any photo images after seed"

    # /albums
    page.goto(demo_base_url + "/albums", wait_until="domcontentloaded")
    assert page._e2e_page_errors == []
    # Album cards should mention at least one of the seeded names.
    body = page.inner_text("body")
    assert "Studio portraits" in body or "Sun + sky" in body, "albums page missing seeded album names"

    # /dislikes
    page.goto(demo_base_url + "/dislikes", wait_until="domcontentloaded")
    assert page._e2e_page_errors == []
    # Dislikes should have at least one photo (we seeded 2).
    imgs = page.query_selector_all("main img, [class*='grid'] img, [class*='result'] img")
    assert len(imgs) >= 1, "dislikes page rendered without any photo images after seed"
