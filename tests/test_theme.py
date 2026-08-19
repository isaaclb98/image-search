"""
tests/test_theme.py — file-content + sanity checks for the theme
system.

We do NOT stand up the full app here — the end-to-end render path is
covered by `tests/test_search_api.py` (which uses the live
`app_with_qdrant` fixture for that file). What this module covers:

  - base.html loads the design-system stylesheets in the sacred
    order (tokens → glass → layout → photo-card).
  - The stylesheets ship from search/static/css (no Tailwind /
    DaisyUI vendor bundle — those were stripped in the T-refactor;
    the CSS directory IS the design system).
  - tokens.css defines the core design tokens the rest of the CSS
    reads from.
  - The nav keeps an accessible active state (aria-current) now
    that the JS-hydrated theme toggle is gone.

If these checks fail the page goes visibly wrong in production —
the contrast/centering/FOUC regressions from weeks 1-2 showed up
exactly like this in the HTML — so the file-level guard is
worth keeping lean.
"""

from __future__ import annotations

import re
from pathlib import Path

_CSS_DIR = Path(__file__).resolve().parent.parent / "search" / "static" / "css"
_BASE = Path(__file__).resolve().parent.parent / "search" / "templates" / "base.html"


def _read(name: Path) -> str:
    return name.read_text()


def _strip_comments(src: str) -> str:
    """Drop /* ... */ blocks and Jinja {# ... #} comments so a
    string-match against a comment occurrence doesn't trip the
    test (the source files use comments to document design
    decisions)."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"\{#.*?#\}", "", src, flags=re.DOTALL)
    return src


# ---------- base.html wiring ----------

def test_base_loads_design_system_css_in_order():
    """base.html must load the design-system stylesheets in the
    documented order: tokens.css FIRST (defines every CSS variable),
    then glass.css primitives, layout.css composition, and
    photo-card.css. Reordering breaks variable resolution."""
    src = _read(_BASE)
    css_hrefs = re.findall(r"asset\('css/([a-z-]+)\.css'\)", src)
    assert css_hrefs == ["tokens", "glass", "layout", "photo-card"], (
        f"base.html stylesheet order drifted: {css_hrefs}"
    )


def test_base_has_no_daisyui_or_tailwind_remnants():
    """The T-refactor stripped Tailwind v4 + DaisyUI v5; the design
    system now ships entirely from search/static/css. Guard against
    silent reintroduction of the vendor bundle (1.7 MB CSS)."""
    src = _strip_comments(_read(_BASE)).lower()
    assert "daisyui" not in src
    assert "tailwind" not in src
    assert "data-theme=" not in src


def test_base_still_loads_alpinejs():
    src = _read(_BASE)
    assert "alpinejs" in src.lower()


def test_base_nav_marks_active_page_for_at():
    """Accessibility contract for the primary nav: the active tab
    carries aria-current=\"page\" (the JS-hydrated theme toggle that
    used to own dynamic aria labels is gone; the nav's active state
    is server-rendered and must stay announced to AT)."""
    src = _read(_BASE)
    assert 'aria-current="page"' in src
    assert "nav-list" in src


# ---------- design tokens ----------

def test_tokens_css_defines_core_design_tokens():
    """tokens.css is the single source of truth for the design
    variables (the old DaisyUI input.css theme bridge was removed
    with the vendor bundle)."""
    src = _strip_comments(_read(_CSS_DIR / "tokens.css"))
    for variable in ("--bg-base", "--fg", "--accent-500", "--border-glass"):
        assert variable in src
    # Variables must be declared, not just referenced.
    assert ":root" in src


def test_no_css_uses_daisyui_plugin_directive():
    """The vendored DaisyUI CSS does not satisfy the plugin-loader's
    signature, so `@plugin \"daisyui/...\"` must never appear in the
    shipped stylesheets."""
    for css in _CSS_DIR.glob("*.css"):
        src = _strip_comments(css.read_text())
        assert "@plugin" not in src, f"{css.name} uses a @plugin directive"
        assert "daisyui" not in src.lower(), f"{css.name} references DaisyUI"
