"""
tests/test_states.py — verifies the _macros.html state macros render
and that page templates opt into them at the right points.

Specifically:
  - page_header(title) renders the right wrapper + count formatting
  - empty_state() renders a centered block with optional icon/title/body/action
  - error_state() renders a DaisyUI alert with the error icon
  - loading_skeleton() renders N skeleton placeholders
  - The 6 pages where the macros are wired (albums/saved/favorites/
    random/centroids/photo) actually invoke them in their empty-or-error
    branches (regression guard).

These are Jinja-syntax checks + the page-bootstrap-level "does the
template reference the macro" check. We do NOT exercise full app.py
routes here — that's covered by the existing test_albums_html /
test_saved_searches_api / test_search_api suites.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# ---------- _macros.html source-level checks ----------

_MACROS_PATH = Path(__file__).resolve().parent.parent / "search" / "templates" / "_macros.html"


def _read_macros() -> str:
    return _MACROS_PATH.read_text()


def test_macros_file_defines_page_header():
    assert "{% macro page_header(" in _read_macros()


def test_macros_file_defines_empty_state():
    assert "{% macro empty_state(" in _read_macros()


def test_macros_file_defines_error_state():
    assert "{% macro error_state(" in _read_macros()


def test_macros_file_defines_loading_skeleton():
    assert "{% macro loading_skeleton(" in _read_macros()


def test_macros_file_defines_blurhash_thumb():
    assert "{% macro blurhash_thumb(" in _read_macros()


# ---------- Jinja env renders the macros in isolation ----------

@pytest.fixture(scope="module")
def jinja_env():
    """Build a Jinja env that can resolve the macro file."""
    import jinja2
    loader = jinja2.FileSystemLoader(str(_MACROS_PATH.parent))
    return jinja2.Environment(loader=loader, autoescape=True)


@pytest.fixture(scope="module")
def macros_template(jinja_env):
    return jinja_env.get_template("_macros.html")


def test_page_header_renders_title(macros_template):
    out = macros_template.module.page_header("Albums")
    assert "Albums" in out
    assert "text-2xl" in out  # Tailwind class — proves we're using the new design system


def test_page_header_renders_count_singular(macros_template):
    out = macros_template.module.page_header("Albums", count=1, count_label="album")
    assert "1 album" in out
    assert "s" not in out.split("album")[1].split("</")[0]  # no 'albums' suffix on singular


def test_page_header_renders_count_plural(macros_template):
    out = macros_template.module.page_header("Albums", count=12, count_label="album", count_plural_label="albums")
    assert "12 albums" in out


def test_empty_state_minimal(macros_template):
    out = macros_template.module.empty_state()
    # Even without args, the wrapper renders (so an empty state is
    # never accidentally blank).
    assert "flex flex-col items-center justify-center" in out


def test_empty_state_full(macros_template):
    out = macros_template.module.empty_state(
        icon="★", title="Nothing here",
        body="Try again later.", action_url="/search", action_label="Search",
    )
    assert "★" in out
    assert "Nothing here" in out
    assert "Try again later." in out
    assert 'href="/search"' in out
    assert "Search" in out


def test_error_state_renders_daisyui_alert(macros_template):
    out = macros_template.module.error_state("Boom!")
    assert "alert alert-error" in out
    assert "Boom!" in out
    assert "role=\"alert\"" in out


def test_loading_skeleton_grid_renders_expected_count(macros_template):
    out = macros_template.module.loading_skeleton(kind="grid")
    # 20 skeleton divs per the macro contract.
    assert out.count("skeleton aspect-square") == 20


def test_loading_skeleton_detail_renders_text_lines(macros_template):
    out = macros_template.module.loading_skeleton(kind="detail")
    assert "skeleton h-8" in out
    assert "skeleton h-4" in out


# ---------- Page templates opt into the macros ----------

_TEMPLATES = Path(__file__).resolve().parent.parent / "search" / "templates"


def _page(name: str) -> str:
    return (_TEMPLATES / name).read_text()


def test_albums_page_uses_page_header_and_empty_state():
    src = _page("albums.html")
    assert "ui.page_header(" in src
    assert "ui.empty_state(" in src


def test_saved_page_uses_page_header_and_empty_state():
    src = _page("saved.html")
    assert "ui.page_header(" in src
    assert "ui.empty_state(" in src


def test_favorites_page_uses_page_header_and_empty_state():
    src = _page("favorites.html")
    assert "ui.page_header(" in src
    assert "ui.empty_state(" in src


def test_random_page_uses_page_header_error_and_empty_state():
    src = _page("random.html")
    assert "ui.page_header(" in src
    assert "ui.empty_state(" in src
    assert "ui.error_state(" in src


def test_centroids_page_uses_page_header():
    src = _page("centroids.html")
    assert "ui.page_header(" in src


def test_photo_page_uses_error_state():
    src = _page("photo.html")
    assert "ui.error_state(" in src


def test_search_page_still_works_without_macros():
    """search.html is the most complex page (chips, prompts, saved bar). It
    is intentionally NOT rewritten in this branch — guard against accidental
    regressions that would break the `test_search_api` suite."""
    src = _page("search.html")
    # search.html uses the view-toggle + result-grid partial; no ui.* macros.
    # The empty/error states for search are intentionally SSR-light: server
    # returns an empty results list + no error. Verify the page still
    # references _result_grid.html so we get the grid styling.
    assert "view-toggle-btn" in src
    assert '{% include "_result_grid.html" %}' in src
