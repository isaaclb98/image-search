# UI Audit + Overhaul Plan — 2026-08-18

> Generated after reading PRINCIPLES.md, DESIGN.md, and rendering every
> page at 1440×1000 against the latest dev server. This is a working
> brief — not a spec.

## TL;DR — what's wrong

The app is at the **90% plateau**: tokens are declared, glass surfaces
are loaded, components exist. But the last 10% — actually *applying*
the system consistently — is missing in roughly a dozen places that
make every page feel like a prototype. The fixes are surgical, not
architectural. Two sprints to ship, then a polish pass.

## Evidence — sampled from live screenshots (`/home/ubuntu/.screenshots/eval-*.png`)

### Pervasive (every page)
1. **Active nav state is a one-off** — `For you` uses a `.glass-pill`
   with blue border, but the rest of nav is plain text. The asymmetry
   reads as "this page is special" instead of "this is the active page."
2. **Type hierarchy collapses across page intros**. `/for-you` uses
   `.page-intro` (subtle text-only); `/` uses a centered hero panel;
   `/albums` and `/discover` use `_macros.page_header` (text only again).
   Three different page-intro primitives for the same job.
3. **Card chrome varies without semantic reason.** `.card.glass--sharp`,
   `.album-card`, `.saved-card`, `.for-you-empty`, `.centroid-card-header` —
   5 ways to render "a card."
4. **Sidebar on `/photo/:id` has 4 different surface treatments** —
   `← Home` link sits above a `glass--sharp` panel which holds
   pill toggles above a `<details>` group and a giant gradient CTA.
   Layering with no rule.

### Composer (`/`) — the high-leverage page
5. **`SAVED` dropdown + `Save current` button share one row but extend
   beyond their container width.** Visible overflow on a 1440 viewport.
6. **INCLUDE / EXCLUDE input rows**: label and "required / optional"
   microcopy sit at *different vertical baselines*, and the trailing
   `+` button floats orphaned. 4 things on one row, no grouping.
7. **"View toggle" (Grid / Feed) is `.glass-pill` × 2**; this is the
   only place a 2-state pill toggle appears. Likely a one-off.
8. **The composer shows no progress indication** for what a click on
   `Search` does — there's a giant blue CTA, but state is silent
   before the grid paints.

### Detail (`/photo/:id`)
9. **Photo panel uses `glass--sharp` but it's the hero of the page**
   — that's the *one* documented use case for `.glass--sharp`. Good.
   But the metadata sidebar also uses `glass--sharp`. Two
   `.glass--sharp` panels visible at once = undefined hierarchy.
10. **`PATH` shown as a literal `<input>` form field**, styled as
    blue-bordered text. Looks form-coded, not metadata-coded.
11. **The "Most similar photos →" CTA is a tall gradient button** at
    the bottom of the sidebar. Competes with page hero. Belongs
    elsewhere (ghost button under metadata, or a meta-row).

### Lists (`/random`, `/favorites`, `/saved`)
12. **Photo grids are clean (column ladder works)** but the cards
    themselves are bare `<img>` with no chrome — no overlay for
    metadata, no hover affordance, no quick-action. Looks unfinished.
13. **`/favorites` shows 0 photos with no empty-illustration**;
    just text. Inconsistent with `/albums` which has a centered
    empty-state card.

### Library (`/albums`)
14. **Create form sits above empty-state card with no visual
    continuity** — the eye doesn't connect "make album → see albums
    here." They should be *the same surface*.
15. **0-albums counter** is right-aligned, far from the create form.
    Disconnected.

### Discover (`/discover`)
16. **Two columns — picker / preview**. The preview pane is half-empty
    when no pick is active. Use of glass hierarchy unclear.

## Root causes

### A. Cascade order is wrong
`base.html` loads `app.css` (compiled Tailwind, 1.7MB) **first**, then
tokens, glass, layout, photo-card. Means Tailwind defaults **win the
cascade** unless a CSS file has a more specific selector or comes
later. Files that load later fight Tailwind on every property.

### B. Design system documents three glass classes but the templates
use at least 7 distinct card surfaces:
- `.card.glass--sharp`
- `.album-card`
- `.saved-card`
- `.for-you-empty`
- `.photo-detail-panel`
- `.centroid-card-header`
- `.centroid-chip` / `.centroid-bar`

Only the first three are accounted for in DESIGN.md. The rest are
bespoke.

### C. Page intro has three implementations
- `search.html`: centered hero panel (`.glass--sharp`)
- `for_you.html`, `albums.html`, `centroids.html`, `discover.html`:
  `{% call ui.page_header(...) %}` macro
- `random.html`, `saved.html`, `favorites.html`, `dislikes.html`:
  ad-hoc headers with eyebrow + h1

One of these is right; two are deprecated; right now all three ship.

### D. Nav has 8 items, no overflow
For you, Centroids, Discover, Random, Favourites, Dislikes, Saved,
Albums. On 360px viewport this wraps. No mobile strategy.

### E. Theme bootstrap script is theatre
`base.html` reads `localStorage.getItem('theme')`, falls back to
`prefers-color-scheme`, then defaults to `dark`. The fallback
expression is `(prefersDark ? "dark" : "dark")` — always dark. The
script exists but does nothing observable. Dead weight signaling
"we support theming."

---

## Overhaul plan — 5 sprints

### Sprint 1 — Fix the foundation (cascade + dead code)

**Goal: tokens.css is the source of truth and dead weight is deleted.**

- [ ] Move tokens.css to load **first** in `base.html` (before
      `app.css`). Single-source-of-truth principle means it ships
      before everything that might override it.
- [ ] Delete the theme-bootstrap IIFE — it's dead code that actively
      lies about light-theme support.
- [ ] Audit Tailwind output: drop unused utilities (check
      `@apply` blocks vs. real usage); target `app.css` < 200KB.
- [ ] Drop `tokens.css` light-mode declaration per Principle 4.

**Verification:**
- Page renders identical to baseline (visual diff nothing).
- `app.css` size shrinks.
- DevTools show tokens variables in computed styles on every
  page.

### Sprint 2 — Unify page intro (3 → 1 macro)

**Goal: every page intro is one macro.**

- [ ] Audit the three patterns (hero panel, page_header macro,
      ad-hoc) and pick the canonical text-only pattern (matches
      DESIGN.md "no glass — just text"). Promote to one macro.
- [ ] Replace every `{% call ui.page_header(...) %}` and ad-hoc
      header to use the same macro.
- [ ] The home page becomes special (search composer, not a list),
      gets the hero panel rule explicitly. Document it.

**Verification:**
- All 11 page intros read as text-only (no glass) except composer
  and a focused detail view.
- Macro is used in 9 of 11 pages; 2 pages are documented exceptions.

### Sprint 3 — Collapse cards (7 → 3)

**Goal: card surfaces reduce to the three from DESIGN.md.**

- [ ] `.card.glass--sharp`, `.album-card`, `.saved-card` all merge
      onto `.card` + optional `--tinted` modifier or similar.
- [ ] `.for-you-empty`, `.photo-detail-panel`, `.centroid-card-header`
      collapse onto `.card` with semantic class names.
- [ ] Photo grid cards (`.photo-card`) adopt the same `.card` primitive
      with `--bare` (no chrome) variant.
- [ ] Confirm glass vocabulary stays at 3 (`.glass`, `.glass--sharp`,
      `.glass-pill`) — no fourth added.

**Verification:**
- Grep `class="card` returns 1 canonical and a few semantic variants.
- No `glass-frost`, `glass-tinted`, or new names appear.

### Sprint 4 — Fix the composer

**Goal: home page reads as one composition, not five sibling components.**

- [ ] `SAVED` and `Save current` move into a single pill cluster, no
      overflow at 1440.
- [ ] INCLUDE/EXCLUDE rows use a single `prompt-row` primitive:
      label / input / trailing action all in one flex row with aligned
      baselines; microcopy moves under the label, not on the right.
- [ ] `+` button is the standard `icon-action` primitive (used
      elsewhere if applicable, e.g. add filter).
- [ ] View toggle (Grid / Feed) promoted to a `segmented` primitive
      (different from glass-pill; orthogonal state).
- [ ] Search CTA gets visible pressed/loading state.

**Verification:**
- Composer fits inside `--container-wide` with no horizontal overflow.
- Adding an INCLUDE row keeps alignment intact.

### Sprint 5 — Photo detail rewrite

**Goal: this page becomes the visual anchor with clear hierarchy.**

- [ ] Move favourite / dislike from top of sidebar to a single
      action row directly under the photo (overlay or meta-row).
- [ ] Sidebar uses ONE `.glass` panel (not `glass--sharp`) — diff
      surfaces signal hierarchy.
- [ ] `PATH` becomes a `<code>` block with copy-on-click, not an
      `<input>` styled like a form field.
- [ ] `Most similar photos` becomes a ghost button under metadata;
      primary CTA (when applicable) is in the action row.
- [ ] Album list becomes a list of `.glass-pill` chips with
      click-to-add (no form-coded chrome).

**Verification:**
- One glass--sharp surface per detail page (the photo).
- Sidebar reads as metadata, not as form.

### Sprint 6 — Nav + consistency audit

**Goal: 8 items, mobile strategy, all-glass.**

- [ ] Active state uses same primitive as `.glass-pill` (the existing
      "tab" appearance), not a one-off border style.
- [ ] Reduce visible nav: hide "Dislikes" behind an overflow menu
      (its data is reachable via /discover → liked).
- [ ] Mobile (≤ 640px): nav becomes a horizontal-scroll pill row.
- [ ] Re-screenshot all pages; compare against the audit baseline.

**Verification:**
- Screenshot diff confirms nav looks the same on every page.
- 360px viewport doesn't wrap.

---

## What I'm explicitly NOT doing

- **Not changing fonts.** Inter is the right call; type ramp is.
- **Not adding light theme.** Principle 4 forbids it.
- **Not rewriting to a SPA.** Jinja + HTMX suits this app; not over-
  engineering.
- **Not touching backend.** All changes are template + CSS + JS.

## Risks

- **Tailwind dependency**. Renaming cards might cause tailwind-utility
  side effects. Test each rename in isolation.
- **The `app.css` regeneration step**. If it's a build artifact, every
  PR must rebuild. Check `package.json`/`Makefile`.
- **Glass on glass**. Two translucent layers on a dark photo = unclear
  depth. Already violates the 5% contrast rule from the
  `glass-ui-design-system` skill.

## Next decision

Approve the audit findings + sprint order, or push back where
the plan overshoots. After approval, I implement Sprint 1 first,
screenshot, then iterate.
