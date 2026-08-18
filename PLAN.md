# Plan

The implementation roadmap. Each phase is a single commit. No phase
ships without the E2E suite green (`pytest tests/e2e/`) and a
screenshot diff against the prior phase's baseline.

The principles live in [`PRINCIPLES.md`](./PRINCIPLES.md). This plan
assumes them; it does not restate them.

## Phase A — Token prune (Principle 3)

**Goal.** The token vocabulary is the ratio-basis that makes Principle 3
mechanically enforceable. Today it isn't — `tokens.css` declares ~150
variables across a 13-stop spacing scale, a 5-stop radius scale, a
4-stop elevation ladder, and a major-third type scale that components
drift from. Cut it to what the codebase actually uses, expressed in
ratios.

**Work.**
- Audit every token in `search/static/css/tokens.css` with `git grep`.
- For each token with one caller: inline the value at the caller,
  delete the token. (A single-caller token isn't a token; it's a value
  with extra steps.)
- For each remaining category, declare the basis and the stops:
  - **Spacing**: 1 unit (`--u`), 4 exposed stops (e.g., 4 / 8 / 16 / 32).
    No other spacing literals anywhere. Hero/control padding derived
    as multiples of the unit.
  - **Type**: major-third ratio (1.250), 5 sizes (xs / sm / base / lg /
    xl). Hero text uses `clamp()` over the xs→xl range. No `font-size:
    Npx`.
  - **Radii**: 3 stops (sm / md / lg), tied to element size class.
  - **Elevation**: 4 stops, named `--shadow-0` through `--shadow-3`.
    No inline `box-shadow`.
  - **Container**: 2 stops, golden-ratio derived. `--container-narrow`
    and `--container-narrow * φ` for hero. No `max-width: 1200px`.
- Update or add a guardrail: a docblock at the top of `tokens.css`
  stating the four principles inline so the next person to write a
  component sees them.

**Acceptance.** Token count down ~30%. No visible regressions in
`tests/e2e/screenshots/`. All 17 E2E tests pass. The codebase can grep
for `var(--space-` and find consistent step values.

**Out of scope.** Glass vocabulary (Phase B). Page-level changes
(Phase C onward). Templates are not touched.

## Phase B — Glass vocabulary cut (Principle 4)

**Goal.** Rule 4 says dark-glass, single theme, minimalist. Today's
five-variant vocabulary contradicts that. Cut to three classes, each
with a documented single use.

**Work.**
- Three classes: `.glass` (default panel), `.glass--sharp` (hero
  panel — Discover + at most one other page), `.glass-pill` (chips,
  toggles, view-segment controls).
- Migrate callers of `.glass-frost` and `.glass-tinted` to `.glass`
  or `.glass--sharp`, whichever fits. The `--tinted` variant was the
  worst offender — it pulled the dominant color from the photo
  underneath, which violated "single theme."
- Rewrite `DESIGN.md` (or replace with a small doc) to document the
  three variants, their use cases, and *the rule for picking
  between them*. A vocabulary without a selection rule decays.

**Acceptance.** Three `.glass-*` classes exist in CSS. The other two
have been deleted. All E2E tests pass. Pixels look at least as good as
before on every page (some need a re-shoot; aim for no regression,
not for "better yet").

**Out of scope.** Spacing (Phase A). Per-page differentiation
(Phase F). Hero redesign (Phase E).

## Phase C — Concrete defects

**Goal.** Three specific pixels-broken things on the current `main`,
each fixable in a single template/CSS edit.

**C.1 — Hide the raw filesystem path on `/photo/{id}`.**
The `PATH` field shows `/tmp/image-search-demo/demo_05.jpg`. That's
an internal detail. Replace with photo-relevant metadata (date,
dimensions) or drop the field.

**C.2 — Stop the ambient mesh bleeding through the photo on
`/photo/{id}`.**
The page-level mesh background leaks through the photo container and
washes the lower half of the viewport. After Phase B this becomes a
single-rule fix: the body behind `.photo-detail` should be opaque or
neutral; only the chrome uses glass.

**C.3 — Replace the "Showing the first N" footer with real
pagination, or delete it.**
Currently every paginated page renders this narration line. It's a
limitation-as-text. Either fix the limit (paginate) or remove the
narration. Pick one and commit.

**Acceptance.** Path field gone. Mesh bleed gone on `/photo/{id}` at
both viewports. "Showing the first N" gone from every page where it
appears. All 17 E2E tests pass.

**Out of scope.** Anything beyond these three. Discovered defects get
filed for Phase F.

## Phase D — `DESIGN.md` rewrite

**Goal.** A single-thesis design doc that codifies the principles and
documents the chosen vocabulary. Replace today's table-of-five-glasses
with a focused reference.

**Work.**
- Lead with the four principles (or link to `PRINCIPLES.md` if that
  file is committed alongside).
- Document the token basis: unit, ratios, the named stops.
- Document the glass vocabulary: three classes, with the rule for
  picking between them.
- Document the spacing basis: when to use 4, 8, 16, 32. What to do
  when none of them fit (rare; document the answer).
- One worked example: take a typical page (e.g., `/random`) and
  annotate it against the doc. If the annotation reveals a violation,
  that's feedback for Phase F — do not fix in this commit.

**Acceptance.** `DESIGN.md` exists, fits in one screen, has no
contradictions with `PRINCIPLES.md`, and references the actual
codebase (specific class names, specific file paths).

**Out of scope.** Implementation changes. Doc only.

## Phase E — Home page model decision

**Goal.** Decide whether the home page is a search composer
(Model A — search input above a responsive photo grid) or a tool
interface (Model B — left-rail controls, right-rail preview).
Build the chosen model.

**Why this comes after D.** Phase D's worked example will reveal
whether the existing home page can be re-shaped under the new
vocabulary or whether it needs to be rebuilt. The decision depends
on what D surfaces.

**Work (Model A assumed; revise if B chosen).**
- Single-row composer: search input + Search button + Surprise.
- Filters collapse into a disclosure; the disclosure state
  survives form submit.
- The "Random picks" row becomes the primary content. Tile
  macro gains: hover affordance, caption row (date, dimensions),
  focus state.
- Remove the 6-control hero bar from `search.html`.
- Re-shoot `/` at desktop and mobile. Acceptance is no scroll on
  1440×900 to see the first row of photos.

**Acceptance.** Home page has one primary action visible above the
fold. FCP unchanged. No regression on `/random`, `/discover`,
`/for-you`, or any other gallery page. All 17 E2E tests pass.

**Out of scope.** Anything that affects pages other than `/`.

## Phase F — Component audit + per-page passes

**Goal.** For every page, eliminate bespoke code and bring the page
into compliance with Principles 1, 2, and 4. This is the long tail
and the principled cleanup.

**Work (per page).**
- Audit the page's CSS classes. Single-caller classes are
  candidates for inlining or promotion. Promote if the same shape
  is needed elsewhere; inline otherwise.
- Audit the page's HTML for inline styles, raw values, raw DOM.
  Every literal goes through a token.
- Audit the page's JS for page-specific helpers that duplicate
  shared code. Pull into shared helpers.
- Re-shoot. Compare to baseline. If a page regresses, revert and
  open a Phase G.

Pages covered (in order):
1. `/random`
2. `/favorites`
3. `/dislikes`
4. `/albums`
5. `/albums/{id}` (album detail)
6. `/saved`
7. `/centroids`
8. `/discover`
9. `/discover/liked`
10. `/for-you`
11. `/photo/{id}`
12. `/login`

**Acceptance.** `git grep` for the page finds no single-caller CSS
class. No inline style attribute on the page. No raw value in any
style or template. All 17 E2E tests pass. Screenshot diff shows
improvement or no change on each page.

**Out of scope.** New pages, new features. Bug fixes discovered
during the audit get filed for Phase G.

## Phase G — Forward bug bash

**Goal.** Everything discovered during Phases A–F that wasn't worth
fixing inline. Likely candidates:
- A11y audit (axe-core in the E2E suite, keyboard-only walk-through).
- Performance audit (Lighthouse via Playwright, FCP/CLS regression
  check).
- Pages or affordances that turn out to be redundant.
- Discover-vs-for-you-vs-saved-searches overlap.

**Acceptance.** TBD per issue. Each issue gets its own commit.

## Sequencing

Strict ordering. No phase starts before the previous phase's
acceptance criteria pass. Each phase is one commit; commits are
small; the diff is reviewable in one screen.

The reason for the order:
- **A before B** because the glass vocabulary is built on top of the
  token basis. Cutting glass before tokens means repainting
  twice.
- **B before C** because C.2 (mesh bleed) is a rule-4 violation
  and the fix depends on the new vocabulary.
- **C before D** because the design doc should reference the actual
  fix-state, not the broken state.
- **D before E** because E's decision depends on what the worked
  example in D reveals.
- **E before F** because F's per-page audit is cheaper once the
  home page is committed to one model.
- **F before G** because bug-bash is for things that surface
  during the principled cleanup.

## What this plan does not do

- Does not change the backend. FastAPI is sacred per the historical
  `PLAN.md` (now deleted) and nothing in this work requires it.
- Does not add Vue, Tailwind, npm, or any new build tooling.
  Stack stays Jinja2 templates + plain CSS + vanilla JS.
- Does not introduce a second theme.
- Does not chase Lighthouse 100. Performance is monitored as a
  regression check, not a target.
- Does not resurrect the deleted `PLAN.md` or any of its prior
  content. If a phase needs reference material, it cites the
  codebase, not the old plan.
