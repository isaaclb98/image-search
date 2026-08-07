"""
tests/test_theme.py — file-content + sanity checks for the theme
system shipped in T3.

We do NOT stand up the full app here — the end-to-end render path is
covered by `tests/test_search_api.py` (which uses the live
`app_with_qdrant` fixture for that file). What this module covers:

  - <html data-theme="..."> is in the rendered base template.
  - base.html loads AlpineJS (drives the toggle).
  - x-cloak is applied to the toggle so it doesn't briefly show
    both sun + moon icons stacked during hydration.
  - input.css uses @import for the vendored DaisyUI (T1 decision: we
    do not use `@plugin "daisyui/..."` because the vendored CSS does
    not satisfy the plugin-loader's signature).

If these checks fail the page goes visibly wrong in production —
the contrast/centering/FOUC regressions from weeks 1-2 showed up
exactly like this in the HTML — so the file-level guard is
worth keeping lean.
"""

from __future__ import annotations

import re
from pathlib import Path



_BASE = Path(__file__).resolve().parent.parent / "search" / "templates" / "base.html"
_CSS = Path(__file__).resolve().parent.parent / "search" / "static" / "css" / "input.css"


def _read(name: Path) -> str:
    return name.read_text()


def _strip_comments(src: str) -> str:
    """Drop /* ... */ blocks so a string-match against a comment
    occurrence doesn't trip the test (input.css uses comments to
    document design decisions)."""
    return re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)


# ---------- base.html wiring ----------

def test_base_emits_data_theme_attr():
    """The `<html>` tag must carry `data-theme="..."` so DaisyUI
    can react to the live switch."""
    assert 'data-theme="' in _read(_BASE)


def test_base_loads_alpinejs():
    src = _read(_BASE)
    assert "alpinejs" in src.lower()


def test_base_has_x_cloak_to_prevent_fouc():
    """x-cloak hides the toggle's stacked sun+moon SVGs until Alpine
    hydrates — without it, both icons flash on every page load."""
    src = _read(_BASE)
    assert "x-cloak" in src


def test_base_carries_an_alpine_directive_to_drive_the_toggle():
    """Alpine's `x-data` / `x-init` is what wires the localStorage
    read-write. Without at least one such directive the toggle
    does nothing on click — guard against silent regressions."""
    src = _read(_BASE)
    assert (
        "x-data" in src
        or "x-init" in src
        or "x-show" in src
        or "$store" in src
    ), "base.html must include an Alpine directive to drive the theme toggle"


def test_base_toggle_has_dynamic_aria_label():
    """Single-button toggle (T3 final polish). The button's
    `:aria-label` is bound dynamically to the Alpine theme state so
    screen-reader users hear "Switch to dark theme" / "Switch to
    light theme" depending on the current value. Without this the
    button is opaque to AT — guard against silent regressions."""
    src = _read(_BASE)
    # Alpine `:aria-label="..."` is the dynamic-binding form. The
    # string `Switch to ... theme` is the user-visible label.
    assert ':aria-label="' in src
    assert "Switch to" in src and "theme" in src

    # Toggle must read the current value (so the conditional is
    # actually checking a live variable, not a constant) — `:title`
    # mirrors `:aria-label` so we expect the same pattern on title.
    assert ':title="' in src


# ---------- input.css theme overrides ----------

def test_input_css_reskins_daisyui_via_css_variables():
    """The design system foundation re-skins DaisyUI's built-in
    `light` and `dark` themes by overriding its CSS variables
    under `[data-theme="..."]`. Guard against the re-skin block
    disappearing (the v1 dark-by-default bug surfaced exactly
    like that)."""
    src = _read(_CSS)
    assert "data-theme" in src  # at least one block scoped to [data-theme=...]
    # The re-skin uses DaisyUI's `color-scheme` / `--color-*` tokens.
    assert "--color-" in src


def test_input_css_does_not_use_daisyui_plugin_directive():
    """We vendor DaisyUI via `@import "./vendor/daisyui.css"`. The
    `@plugin "daisyui/theme"` form was rejected because vendored
    CSS files don't satisfy DaisyUI v5's plugin-loader signature."""
    src = _strip_comments(_read(_CSS))
    assert "@plugin \"daisyui" not in src
    assert "@plugin 'daisyui" not in src
    assert "@import" in src  # we still use imports for vendor CSS
