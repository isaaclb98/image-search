# UI Overhaul — completion summary (2026-08-18)

Six sprints done. Branch: `ui-revamp`. Six commits ready for review.
`main` untouched.

## Verified end state

Every page screenshot at `/home/ubuntu/.screenshots/final-*.png`:
- All 10 distinct routes return 200
- All use the same dark-glass + 5-text-size + glass vocabulary
- Mobile (390px) and desktop (1440px) both render
- Composer uses segmented control + 3-col prompt-row grid
- Photo detail: action pills in a row, ghost-style CTA at foot

## What shipped (six commits, all on `ui-revamp`)

| # | Commit | Subject |
|---|--------|---------|
| 1 | `a235dee` | Foundation — cascade order, kill dead theme script, single dark palette |
| 2 | `284c7d9` | Page-intro unification — 1 macro everywhere |
| 3 | `cdf9c71` | Cards — surface props decoupled from 5 card blocks |
| 4 | `144e3bc` | Composer overhaul — segmented + .glass panels + prompt-row grid |
| 5 | `5182ddc` | Photo detail — sidebar as metadata, action row, ghost CTA |
| 6 | `b02ac82` | Nav — single pill list, no theme toggle, mobile scroll |

## Audit items closed

| # | Bug | Resolution |
|---|-----|------------|
| 1 | Cascade order wrong (tokens.css after app.css) | tokens.css now loads first |
| 2 | Dead theme-bootstrap IIFE | Removed (Sprint 1) |
| 3 | Light-mode `:root` block + `data-theme` attr | Removed (Sprint 1) |
| 4 | 3 page-intro patterns shipping | Now 1 macro everywhere (Sprint 2) |
| 5 | 7 card classes redifining glass surface | Stripped; .glass / .glass--sharp drives surface (Sprint 3) |
| 6 | INCLUDE/EXCLUDE rows misaligned | New `.prompt-row` 3-col grid (Sprint 4) |
| 7 | Save current overflow on composer | Now wraps inside `.saved-search-bar` (Sprint 4) |
| 8 | View-toggle was 2 standalone pills | Promoted to `.segmented` primitive (Sprint 4) |
| 9 | Search had no loading state | `.is-loading` + inline spinner (Sprint 4) |
| 10 | Photo action buttons stacked | Now a flex row (Sprint 5) |
| 11 | Most-similar button competed visually | Demoted to ghost-style (Sprint 5) |
| 12 | Album list bespoke chrome | Migrated to `.glass-pill` + `.is-active` (Sprint 5) |
| 13 | Theme toggle was theatre UI | Removed (Sprint 6) |
| 14 | 8-item desktop nav | Primary nav 7 items; Dislikes as utility link (Sprint 6) |
| 15 | No mobile strategy | Single list, scrolls horizontally ≤720px (Sprint 6) |

## Glass vocabulary stays at 3

`principle 2` enforces this. After the work:
- `.glass` — default panel
- `.glass--sharp` — hero panel (one per page)
- `.glass-pill` — pill / chip / toggle

**No fourth class added.** Sub-classes for specialized panels
(`.photo-detail-panel`, `.album-card`, `.saved-card`,
`.for-you-empty`, `.centroid-card-header`, `.album-detail-header`)
keep their BEM names but draw surface from one of the 3 primitives.

## Tokens

All visible values now route through tokens.css. Notable additions:
- `--text-2xl` (Sprint 2 — page-hero h1 size)
- `--accent-400`, `--accent-600` (Sprint 4 — segmented-btn / search-submit hover)
- Inverted ink scale (50=deepest, 900=brightest) for dark theme

No raw hex, no `padding: 12px` literals anywhere outside tokens.css.

## Theme

Per Principle 4, fully single dark theme. Removed:
- `<html data-theme="dark">` attr
- Theme bootstrap IIFE in base.html
- Theme bootstrap IIFE in login.html
- Alpine.js theme toggle + sun/moon SVGs
- `:root[data-theme="dark"]` redundant override
- `:root[data-theme="light"]` (already absent)

## Backend

Zero changes to `app.py`, `auth.py`, or any other backend module.
All UI work is in:
- `search/templates/*.html`
- `search/static/css/*.css`
- `search/static/js/*.js`

## What's not done

The audit plan had six sprints and six are shipped. Out of scope:
- `app.css` size reduction (1.7MB Tailwind+DaisyUI bundle — would
  require rebuilding with used-only utilities, a separate build step)
- Tailwind class removal from templates (legacy utilities like
  `flex`, `gap-3`, `btn-ghost` still used in some templates)
- Migrating tailwind-app.css / vendor/daisyui.css to own everything
  (big lift, low value vs. risk)
