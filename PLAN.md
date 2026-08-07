# PLAN.md — feature/polished-ui-tasks

> The foundation plan below is historical context from the first UI pass.
> The active Phase 4 objective at the end of this file supersedes the earlier
> branch-2 deferral and is the acceptance contract for the current changes.

## Goal

Branch 1 of 2 for "polished, professional UI." Lays the **static design-system foundation** plus the **LQIP data layer**. Branch 2 (`feature/polished-ui-interactive`, sketched in Out of scope) builds the lightbox, hover-stack, grid consolidation, and keyboard shortcuts on top.

Scope is mostly **CSS + Jinja + one indexer-payload field**. No new view functions, no new routes, no JS-framework replacement. Adds Alpine.js for theme toggle; HTMX held for branch 2.

## What's already in place

- FastAPI + Jinja2 + hand-rolled `static/css/site.css` (1648 lines).
- Inter font already loaded in `base.html`.
- `_result_grid.html` already exists as a shared partial.
- `albums.html` + `saved.html` already render as lists, not photo grids.
- Per-page JS in `static/js/`. Kept untouched.
- 445 tests passing on `main`. Branch must not regress.

## Branch split (Isaac, 22:37 EDT)

- **Branch 1 — `feature/polished-ui-foundation`** (this plan): static layer + LQIP.
- **Branch 2 — `feature/polished-ui-interactive`** (out of scope): lightbox, hover-stack, grid consolidation, shortcuts overlay. Depends on Alpine.js + DaisyUI + LQIP from branch 1.

Reason for the split: lightbox, hover-stack, and shortcuts all need the Alpine.js + DaisyUI foundation. Foundation lands first as a smaller, reviewable PR; interactive layer follows on top.

## Files touched

- `tailwind.config.js` (new) — design tokens, DaisyUI plugin, content paths.
- `static/css/input.css` (new) — Tailwind directives + DaisyUI + custom layer.
- `static/css/app.css` (new, generated) — Tailwind output. **Gitignored.**
- `static/css/site.css` (delete) — replaced by Tailwind output.
- `bin/tailwindcss` (new, OS-specific binary) — **Gitignored.**
- `templates/base.html` — Tailwind link, theme bootstrap, Alpine.js CDN.
- `templates/_macros.html` (new) — reusable partials: photo card, collection card, page header, four-state blocks, blurhash thumb.
- `templates/_theme_toggle.html` (new) — Alpine.js component.
- All page templates — apply tokens, design four states, integrate theme toggle.
- `indexer/blurhash.py` (new) — compute blurhash during indexing.
- `indexer/vision_encoder.py` — call blurhash at index time.
- `indexer/upsert.py` — store `blurhash` in Qdrant payload.
- `indexer/indexer.py` — add `--reblurhash` subcommand for backfill.
- `pyproject.toml` — add `tw:watch` / `tw:build` scripts; add `blurhash` to deps.
- `.gitignore` — `bin/`, `static/css/app.css`.
- `README.md` — document Tailwind dev workflow + `--reblurhash` backfill note.

## Tasks

### Phase 1 — Foundation (static layer)

**T1. Tailwind standalone CLI + DaisyUI**

- Download `tailwindcss` standalone binary per OS into `bin/tailwindcss` (gitignored).
- `tailwind.config.js` scans `templates/**/*.html` + `static/js/**/*.js`.
- DaisyUI v5 plugin loaded.
- Two custom DaisyUI themes: `light` (warm-neutral) + `dark` (layered greys, not pure black).
- `static/css/input.css` — Tailwind directives, DaisyUI, custom layer for tokens Tailwind doesn't model.
- `pyproject.toml` scripts: `tw:watch` → `bin/tailwindcss -i static/css/input.css -o static/css/app.css --watch`; `tw:build` → same without `--watch`.
- `.gitignore` — `bin/`, `static/css/app.css`.

**T2. Design tokens**

Define in `tailwind.config.js` `theme.extend`:

- **Color:** warm-neutral scale (50–950), one accent hue, semantic (success / warn / error).
- **Spacing:** Tailwind 4/8px default.
- **Type scale:** 12 / 14 / 16 / 18 / 20 / 24 / 32 / 48. **Tabular numerals on counts.**
- **Radius:** sm 4, md 8, lg 12, full.
- **Shadow:** subtle three-tier (xs / sm / md) — used for hover-raise only, not chrome.

Two DaisyUI themes:
- `light`: bg `#fafaf9`, surface `#ffffff`, text `#18181b`, accent `#3b82f6`.
- `dark`: bg `#0a0a0a`, surface `#141414`, elevated `#1f1f1f`, text `#fafafa`, accent `#60a5fa`.

**T3. Theme system (light + dark + toggle, no FOUC)**

- DaisyUI's `data-theme` attribute on `<html>`.
- Alpine.js component reads/writes `localStorage.theme`, falls back to `prefers-color-scheme`.
- Inline `<script>` in `base.html` sets `data-theme` **before first paint** (FOUC prevention).
- Theme toggle in header (sun / moon icon swap).

**T4. Typography**

- Inter 400 / 500 / 600 already loaded. Add 700.
- Display tracking `-0.02em` on `h1` / `h2`. Body line-height 1.6, heading 1.2.
- `font-variant-numeric: tabular-nums` on count displays.

**T5. Header + nav polish**

- Sticky DaisyUI `navbar`.
- Brand left, nav links right, theme toggle far right.
- Current-page highlight via server-set `data-current-nav` on `<body>` per route.
- Subtle bottom border; no heavy shadow.
- Mobile: hamburger drawer (DaisyUI `drawer`).

**T6. Photo grid polish**

- Edge-to-edge thumbs (image fills card; metadata below).
- No card border; hover raises `shadow-md → shadow-lg`.
- 4/8px gap between cards.
- Tabular numerals on counts.

**T7. State design per page (loading / empty / error / ready)**

Reusable partials in `templates/_macros.html`:

- `{% macro loading_skeleton(kind='grid') %}` — N skeleton cards for grid pages; skeleton block for detail pages.
- `{% macro empty_state(icon, title, body, action_url, action_label) %}` — centered icon + heading + body + action button.
- `{% macro error_state(message) %}` — neutral error display.
- `{% macro page_header(title, count, count_label) %}` — sticky page header with title + tabular-numeral count.

Apply to every page. Errors raised in view functions caught by a Starlette exception handler that renders the error state.

**T8. Collection cards for `/albums` and `/saved`**

- `/albums` items → DaisyUI `card` with cover thumb (latest photo in album), album name, photo count, hover preview.
- `/saved` items → DaisyUI `card` with query text, result count, last-used timestamp.
- Hover raises shadow; click enters the existing photo-grid page scoped to that collection.

### Phase 2 — Indexer / LQIP data layer

**T9. LQIP / Blurhash compute + payload + backfill**

- New `indexer/blurhash.py` — `compute_blurhash(path: str, x_components=4, y_components=3) -> str`. Output: ~25-40 char base83 string per photo.
- `indexer/vision_encoder.py` calls blurhash alongside the SigLIP2 embedding.
- `indexer/upsert.py` stores `blurhash` in Qdrant payload as **non-indexed** field (`index: false` in the schema setup, if any; or simply not used in a payload index). ~30 bytes per point → ~43 MB extra across 1.5M photos. Negligible vs. embeddings.
- New indexer subcommand: `python -m indexer.indexer --reblurhash`. Walks Qdrant points missing `blurhash`, computes + writes the field, no re-embed. Cheap enough to run on 1.5M in a few hours.
- `pyproject.toml` — add `blurhash>=1.0` to dependencies.

**T10. Blurhash thumb macro (render)**

- New `{% macro blurhash_thumb(photo) %}` in `_macros.html`. Renders a `<div>` with `data-blurhash="..."` and a placeholder background; cross-fades to the real `<img>` on load.
- Used by the photo card macro in T6.
- **No client-side blurhash decoder in branch 1.** Plain CSS background-color fallback (a low-saturation tint derived from the hash) until real decode ships in branch 2's lightbox. Branch 1 ships the data; the rich decode ships with the interactive layer.

### Phase 3 — Validation

**T11. Tests**

New tests:
- `tests/test_blurhash.py` — `compute_blurhash` returns deterministic output for a fixture; `--reblurhash` backfills missing fields.
- `tests/test_theme.py` — `base.html` contains the FOUC-prevention inline script; theme toggle component references `localStorage` correctly.
- `tests/test_states.py` — every page template renders all four states under fixture conditions.

**T12. CI + docs**

- CI build job runs `tw:build` and fails if `app.css` is stale.
- README updated:
  - Tailwind standalone CLI download step (per-OS link from tailwindcss.com).
  - `pip install -e ".[dev]"` does NOT fetch the binary — explicit step.
  - `tw:watch` during dev; `tw:build` before commit.
  - **Backfill note:** existing photos need `--reblurhash` after this branch lands. Estimated hours at 1.5M scale; safe to run independently of search traffic.

## Acceptance criteria

- **AC1.** `pytest tests/ -q` exits 0; existing 445 tests + new tests pass.
- **AC2.** `git diff --stat main..HEAD` scope: `tailwind.config.js`, `static/css/`, `templates/`, `indexer/`, `pyproject.toml`, `README.md`, `.gitignore`, deleted `static/css/site.css`. **No changes to `static/js/*.js`. No new routes. No changes to existing view functions.**
- **AC3.** Light + dark themes render across all pages. Theme toggle persists across reloads. **No FOUC on first paint** when `localStorage` differs from `prefers-color-scheme`.
- **AC4.** Every page template renders all four states (loading skeleton / empty / error / ready). Empty states have icon + copy + action. Loading states match final layout shape.
- **AC5.** `/albums` and `/saved` render as DaisyUI collection cards. Click a card → enters the photo-grid page scoped to that collection.
- **AC6.** Photo grid: edge-to-edge thumbs, hover raises shadow, no borders, tabular numerals on counts.
- **AC7.** Blurhash computed at index time, stored in Qdrant payload. `--reblurhash` backfills existing photos in place.
- **AC8.** CI runs `tw:build` and fails on stale CSS.
- **AC9.** Sentinel review verdict: PASS.

## Assumptions & decisions

- **Branch name:** `feature/polished-ui-foundation`.
- **Tailwind standalone CLI, not Node / npm.** Keeps the no-toolchain posture. Binary gitignored; team commits `tailwind.config.js` + `input.css` only.
- **DaisyUI over hand-rolled components.** Cards / modals / drawers / tabs / themed inputs come for free.
- **Alpine.js for theme toggle only.** Branch 1 is static. HTMX comes in branch 2.
- **Blurhash is the LQIP choice.** ~30 bytes/photo × 1.5M = ~43 MB extra payload, indexed off. Smaller than embedding storage by ~100×. Cheap.
- **`--reblurhash` is a separate indexer subcommand.** Doesn't re-embed; just fills the missing payload field. Runs independently of search traffic.
- **No client-side blurhash decoder in branch 1.** Plain CSS background tint placeholder until branch 2's lightbox ships the real decode. Branch 1 ships the data plumbing; branch 2 ships the rendering polish.
- **Visual regression is not tested in CI.** Would require Playwright / Percy; out of scope. Isaac reviews visually — screenshots in PR description (light + dark, key pages).
- **Sentinel scope:** tests green, no Python behaviour regressions in existing routes, themes work, no FOUC, no accessibility regression (semantic HTML preserved; ARIA on new interactive elements). Visual taste is Isaac's call, not Sentinel's.
- **Git workflow:** new feature branch (per GOALS.md). Hyperion never merges to `main` without Isaac's explicit greenlight, even with Sentinel PASS.

## Out of scope (historical branch 2 — superseded by Phase 4)

Sketched only; gets its own PLAN.md when it starts.

- **T10.** Lightbox component (full-screen overlay, side metadata panel slide-in). HTMX for server-swapped metadata.
- **T11.** Hover-stack preview (hover cycles through neighbours).
- **T12.** Grid consolidation (URL-param scope on `_result_grid.html`, sticky filter chips).
- **T13.** Keyboard shortcuts + `?` overlay (global keydown listener, chord-style `g`+`x`).
- **T14.** Client-side blurhash decoder (vendored `static/js/blurhash.js`); integrate into photo card macro.
- **T15.** Tests + CI for branch 2.

## Out of scope (general)

- Filesystem watcher (inotify / Watchdog) for real-time deletes. Different branch.
- New Qdrant collections / multi-tenancy.
- Authentication / user accounts.
- Face clustering / "People" page (separate ML branch).
- World maps / Places (GPS sparse on personal libs).
- Live Photos, WebDAV.
- Screenshot-based visual regression in CI.

## Phase 4 — UI coherence and interactive polish (Isaac authorization, 2026-08-06)

This phase implements the complete UI pass requested after the live review of
`feature/polished-ui-tasks`. It supersedes the earlier branch-2 deferral for the
interactive pieces while preserving the FastAPI + Jinja + Tailwind standalone
CLI + DaisyUI architecture.

### Objective

Deliver a coherent, product-grade photo-search UI across light and dark themes,
desktop and mobile layouts, with the search workflow as the primary surface and
photo inspection as a fast, keyboard-accessible interaction.

### Scope

- Map/remove legacy dark-only CSS variables and establish one theme-aware token
  system for surfaces, text, borders, focus, accent, and motion.
- Make the header/navigation responsive with a mobile drawer and consistent
  active-route treatment.
- Recompose the search surface: primary query first, advanced filters grouped,
  active modifiers visible, and no narrow-viewport overflow.
- Standardize page headers, buttons, icons, empty/error/loading states, and
  typography across search, photo, albums, saved, random, centroids, and
  discovery pages.
- Add a photo lightbox with metadata/action rail, keyboard navigation, escape/
  focus handling, and quick favorite/album actions that reuse existing APIs.
- Add client-side blurhash decoding for result thumbnails, preserving the
  image-first loading behavior.
- Add keyboard shortcuts/help overlay and persist the selected result view.
- Keep routes and API response contracts stable unless a UI-only endpoint is
  required for existing behavior; do not add authentication or deployment work.

### Acceptance criteria

- Light and dark themes are internally consistent across every page; no legacy
  dark-only `--bg`/`--fg`/`--accent` values leak into light mode.
- At 390px viewport width, the app has no horizontal overflow, search controls
  remain usable, and all primary navigation remains reachable.
- Search has a clear primary action; advanced controls are discoverable without
  dominating the initial layout; active filters remain visible and removable.
- Every result tile supports a consistent lightbox entry point, favorite state,
  keyboard focus, and blurhash-to-image transition.
- Lightbox supports open/close, previous/next, Escape, arrow keys, focus
  restoration, reduced-motion behavior, and a usable narrow layout.
- All existing API, route, and indexer tests continue to pass; add focused tests
  for new template/keyboard/state contracts where deterministic.
- Tailwind output is rebuilt and `git diff --check` is clean.
- Sentinel independently reviews the final diff and returns PASS or concrete
  findings are repaired before completion.

## Phase 5 — Search Diversity overhaul (Isaac authorization, 2026-08-07)

### Objective

Improve the search-only `Diversity` feature for this personal image library.
Discovery is explicitly out of scope and must not be changed.

### Product contract

- `diverse=false` preserves ordinary relevance-ranked search.
- `diverse=true` returns relevant results with fewer exact/near duplicates and
  broader visual/semantic coverage.
- Diversity is applied to the complete candidate ordering before pagination.
- The same query/filter/mode produces a stable ordering during a short-lived
  result session; pages must not overlap.
- Favorites, collections, filename filters, and centroid searches remain
  ordinary search filters. Diversity is not silently ignored when combined
  with them.
- Discovery code and behavior are excluded from this phase.

### Implementation scope

- Add a shared search-only Diversity ranking pipeline with vectorized scoring,
  calibrated relevance, a relevance floor, and adaptive candidate expansion.
- Add exact and perceptual image fingerprints to index payloads, plus a safe
  backfill command for existing Qdrant points.
- Collapse duplicate groups before semantic reranking and expose ranking
  metadata in the API response.
- Cache the ordered Diversity result IDs briefly so offset pagination slices a
  single ranked set.
- Replace the binary search control with an explicit Diversity strength while
  retaining the `Diversity` name.
- Add endpoint, ranking, fingerprint, pagination, and template tests.

### Acceptance criteria

- Exact duplicate IDs never repeat in a Diversity result set.
- Diversity page 2 does not repeat page 1 and does not rerank independently.
- Diversity ordering is stable for the same query, filters, and strength.
- Balanced Diversity preserves a relevance floor while reducing near-duplicate
  results; higher strength increases coverage without random sampling.
- `diverse` and its applied metadata accurately describe the actual behavior.
- Existing non-Diversity search, Discovery, and all existing tests remain green.
- `git diff --check`, compile checks, and applicable lint/build checks pass.
- Sentinel independently reviews the final diff before completion.
