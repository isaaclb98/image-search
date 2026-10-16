# Frontend Refactor Plan

A full sweep of the SvelteKit frontend under
`~/projects/image-search/frontend/`, scoped to behaviour preservation:
no visible changes, no API contract changes, all gates green
(`npm run check` / `test:unit` / playwright) between phases.

## Baseline (captured 2026-10-16)

- Svelte 5 (runes) + SvelteKit 2 + Vite 5 + Vitest 2 + Zod 4
- 11,717 lines in `src/` excluding generated
- 9 routes, 24 components, 6 unit-test files (72 tests),
  32 Playwright e2e tests (5,706 lines)
- Gates: `npm run check` 0 errors / 22 warnings, `npm run
  test:unit` 72/72 pass
- **`npm run lint` is broken** — `eslint` config missing
  (ESLint v10 requires `eslint.config.js`)
- Generated `src/lib/api/types.gen.ts` (2,360 lines) and
  `src/lib/api/schemas.ts` (192 lines) are codegen output,
  not touched by hand

## Repo conventions observed (already in place)

The codebase is **already well-modularized**. Established patterns:

1. **Pure logic in TS modules, components thin.** Examples:
   `lightbox-pagination.ts` (66 lines, fully unit-tested) drives
   the Lightbox infinite-nav state machine. `dropdown-layout.ts`
   (96 lines) drives the Dropdown's viewport-bounded positioning.
   `blurhash-bg.ts` (124 lines) decodes blurhash to data URLs.
2. **Unit tests for pure logic, E2E for component behaviour.**
   `lightbox-pagination.test.ts` is the canonical example.
3. **OpenAPI-driven types**: `openapi.json` → `types.gen.ts` via
   `npm run gen:types`; runtime validation via Zod schemas in
   `schemas.ts` (auto-generated).
4. **Centralised constants**: `lib/api/limits.ts` exports
   `GRID_PAGE_SIZE`; consumed by every grid page.
5. **Design tokens**: `lib/styles/tokens.css` is the single
   source for colour / spacing / radius / type / shadow.
6. **Module-scope dialog store**: `Dialog.svelte` exposes
   `dialog.confirm()` / `dialog.prompt()` so any page gets a
   styled, accessible confirmation without scattering
   `window.confirm` calls.

## What's already good (no work)

- The single most heavily-imported file is `lib/api/endpoints.ts`
  (20 import sites). All API calls go through it.
- PageHeader is the standard title block on every page.
- PhotoGrid is the single canonical photo grid (was consolidated
  from SearchGrid + ForYouRow in round 27).
- Zod dev-mode validation catches API drift.
- Round 86+ history shows the team has been consolidating
  (Albums/photo-page additions re-use Lightbox patterns).

## What's wrong (the refactor targets)

After reading the entire `src/` tree plus every test, here are
the concrete pain points, ordered by payoff:

### 1. Likes and Dislikes pages are 80% identical

`routes/albums/likes/+page.svelte` (150 lines) and
`routes/albums/dislikes/+page.svelte` (132 lines) are
near-clones. Same `Item` type, same `loading/loadingMore/
offset/hasMore` state, same `load()` / `loadMore()` flow,
same optimistic-with-rollback handler, same template
skeleton. The only real differences: which API function,
which button label, and what to call the action. About
200 lines that should be one shared loader + 30-line
page wrappers.

### 2. Like/Dislike toggle is reimplemented in 4 places

The same optimistic-with-rollback toggle lives in:

- `routes/+page.svelte` `onToggleFavorite` / `onDislike`
- `routes/for-you/+page.svelte` `onToggleFavorite` /
  `onDislike`
- `lib/components/Lightbox.svelte` `toggleFavorite` /
  `toggleDislike` (with mutual-exclusion logic)
- `routes/photo/[id]/+page.svelte` `toggleFavorite` /
  `toggleDislike` (with mutual-exclusion + explicit
  unlike-on-like)

All four have the same shape: snapshot state, call API,
on failure roll back. Extract to a `usePhotoFeedback()`
composable in `lib/composables/photoFeedback.ts`.

### 3. "Add to album" is reimplemented in 3 places

`ensureAlbumsLoaded`, `refreshMembership`, and
`addPhotoToAlbum(albumId, pointId)` appear in:

- `lib/components/Lightbox.svelte`
- `routes/photo/[id]/+page.svelte`
- `lib/components/ImageContextMenu.svelte` (lightweight
  variant — only `addPhotoToAlbum` + lazy `listAlbums`,
  no membership set)

Extract to a `useAlbumMembership(pointId, albums)`
composable. The Dropdown component already accepts a
`memberOf: Set<number>` so the composable just owns
the set.

### 4. 5 raw `fetch()` calls bypass the API client

`grep` found 5 sites calling `fetch('/api/...')` directly
instead of `apiGet()`:

- `routes/photo/[id]/+page.ts:27` — `/api/photo/{id}` loader
- `routes/+page.svelte:331` — `/api/system/status`
- `routes/+page.svelte:364` — `/api/admin/index/status`
- `routes/albums/+page.svelte:82-83` — `/api/favorites` +
  `/api/dislikes` (twice, parallel)

These skip Zod dev validation and the error-handling
uniformity of the `apiGet` wrapper. Add proper
endpoints to `lib/api/endpoints.ts`:

- `getSystemStatus()`
- `getAdminIndexStatus()`
- `getFavoriteCount()` (returns `total`)
- `getDislikeCount()` (returns `count`)
- (The `+page.ts` loader for `/photo/{id}` is server-side
  — different concern, see below.)

### 5. `POOL_STOPS` is duplicated between home + for-you + AdditionalFilters

`routes/+page.svelte:105` and
`lib/components/AdditionalFilters.svelte:67` both define
`POOL_STOPS = [200, 1_000, 5_000, 20_000, 50_000]`. The
home page comment even acknowledges the duplication:
"POOL_STOPS also lives in AdditionalFilters (the
Surprise section is rendered there now) so the slider
markup and the size-array stay in sync."

`routes/for-you/+page.svelte:66` has its own *different*
variant `[200, 500, 1_000, 2_000, 10_000, 50_000]` with
its own defaults. The comment says "temperature / pool
bounds mirror the home-page Surprise Me sliders" but
they don't actually mirror.

Move all of this into `lib/api/limits.ts`:

```ts
export const POOL_STOPS_HOME = [200, 1_000, 5_000, 20_000, 50_000] as const;
export const POOL_STOPS_FOR_YOU = [200, 500, 1_000, 2_000, 10_000, 50_000] as const;
export const POOL_SIZE_MAX = 50_000;
export const POOL_SIZE_MIN = 200;
export const DEFAULT_POOL_INDEX_HOME = 2;
export const DEFAULT_POOL_INDEX_FOR_YOU = 3;
```

This also addresses the standing "consolidate duplicates"
goal and gives For-You a real "mirroring" rather than
parallel-but-divergent constants.

### 6. Home page `buildSearchParams` is duplicated within the file

`routes/+page.svelte` has `reload()` and `loadMore()`,
both of which build the same `SearchParams` object with
the same `surprise*` + `collections` + `centroid` logic.
The two objects differ only in `offset` and the
`surpriseSession` initial state. Extract a
`buildSearchParams({ offset })` helper inside the page,
or move to a `useSearchState()` composable (see below).

### 7. URL ⇄ state sync is hand-rolled in the home page

`readFromUrl` / `writeToUrl` (40 lines) live in
`+page.svelte`. The same pattern probably appears in
other pages (need to confirm by reading the rest of
the routes). If 2+ pages have it, extract to
`lib/state/urlState.ts` — a tiny Svelte 5 helper that
takes a state object and serialises it to/from the URL.

### 8. Lightbox has 6+ extractable concerns

`Lightbox.svelte` (1013 lines) is big but 70% of the
size is CSS and prose docs. Of the ~620 lines of
script, these are clearly extractable as composables
or sub-components:

- **Slideshow timer** (lines 308-338) — `playing`,
  `pausedByUser`, `setInterval` effect. → `useSlideshow(items, intervalMs)`.
- **Keyboard nav** (lines 532-609) — `onKey` with
  arrows, space, home/end, pageup/pagedown, F for like.
  → `useKeyboardNav({ onNext, onPrev, onClose, onPlay, onLike })`.
- **Photo preloader** (lines 408-483) — adjacent photo
  preload with save-data check. → `usePhotoPreloader(items, current, isPlaying)`.
- **Blurhash tint + crossfade** (lines 129-145, 344-397)
  — the gated opacity dance. → `useBlurhashTint(blurhash)`.
- **Add-to-album membership** (already covered in #3).

This is the most-invasive change in the plan — the
Lightbox is the single most complex component. Each
extraction should leave the Lightbox itself thinner
and the helpers individually unit-testable (following
the `lightbox-pagination.ts` precedent).

### 9. Two `+page.ts` loaders likely need to be replaced

`routes/photo/[id]/+page.ts` does a server-side
`fetch('/api/photo/{id}')` for the photo page. This is
a universal loader running in SvelteKit's SSR/CSR
boundary — a different concern from the in-component
fetches. Need to read it before deciding the fix
(possibly: a `getPhoto(id)` server function in
`lib/api/server.ts`).

### 10. ESLint config is missing

`npm run lint` errors out with "ESLint couldn't find an
eslint.config.(js|mjs|cjs) file." The `lint` script
exists in `package.json` but never works. Add a minimal
ESLint 9 flat config with `eslint-plugin-svelte` +
`typescript-eslint`. This is the cheapest "improve
code quality" win: a working lint catches drift.

### 11. Hardcoded CSS values that bypass the design tokens

`grep` for `rgba(...)` and `#[0-9a-f]{3,6}` in component
styles found several bypasses. Examples:

- `PhotoTile.svelte` has
  `box-shadow: 0 1px 2px rgba(0,0,0,0.04), 0 1px 3px rgba(0,0,0,0.06);`
  which is exactly `var(--shadow-1)`.
- `PhotoTile.svelte` has
  `box-shadow: 0 8px 16px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.06);`
  which is exactly `var(--shadow-3)`.
- `PromptChips.svelte` has
  `box-shadow: 0 1px 2px rgba(0,0,0,0.04);` which is
  `var(--shadow-1)`.
- `AdditionalFilters.svelte` has fallback values like
  `var(--bg-1, rgba(0, 0, 0, 0.06))` — the fallback is
  dead code (the var is always defined in `tokens.css`).

Walk every component, replace hardcoded values with
the matching token. Add a small eslint rule or grep
script that fails CI if a hardcoded value sneaks back.

### 12. Some smaller items worth catching

- `routes/photo/[id]/+page.svelte` `formatBytes` /
  `formatDimensions` — pure functions, should live in
  `lib/format.ts` so other pages can reuse.
- `lib/api/limits.ts` could also export the
  `SLIDESHOW_PRESETS` from `stores/preferences.ts`
  (currently private to the store). The presets are
  used in the Settings page; exposing them via limits
  would mean the store only owns the *value*, not the
  *list of valid values*.

## Phase plan (one sweep on `dev`)

Following the backend refactor's playbook: one sweep,
no stacked PRs, all gates green between phases,
no behaviour changes, commit + push per phase.

### Phase 0: Pre-flight — ESLint config + green baseline (no behaviour change)

**ESLint config**: ESLint 9 flat config with
`eslint-plugin-svelte` + `typescript-eslint` + `globals`.
**Fail on warnings** — every rule emits a warning or
error; no rule silently allows. Generated files
(`.svelte-kit/`, `build/`, `node_modules/`, `static/`,
`types.gen.ts`) are excluded.

**Lint baseline fix-up**: first lint pass on the
existing tree surfaces **89 problems (43 errors, 46
warnings)**, all mechanical and behaviour-preserving.
The breakdown:

- `no-unused-vars` (35): mostly `catch (e)` bindings
  (the repo's fire-and-forget pattern) — rename to
  `catch (_e)`. A handful of genuinely-unused imports
  get removed.
- `no-empty` (18): empty `catch {}` blocks become
  `catch { /* noop */ }` (or a one-line comment when
  there's a real reason for the catch).
- `no-navigation-without-resolve` (13): every
  `goto(...)` call that doesn't need to be awaited
  either becomes `await goto(...)` or carries an
  `// eslint-disable-next-line` with a one-line
  reason (e.g. fire-and-forget navigation).
- `no-explicit-any` (11): each `as any` cast is
  reviewed; replaceable cases get a real type, the
  rest get an inline disable with a justification.
- `svelte/prefer-svelte-reactivity` (9):
  `new Map(...)` / `new Set(...)` instances inside
  `$state` become `new SvelteMap(...)` /
  `new SvelteSet(...)` so mutations trigger
  reactivity. (The Svelte 5 reactivity boundary is
  the actual reason this matters — without it, a
  `map.set(...)` inside an event handler does not
  re-render.)
- `svelte/require-each-key` (1), `prefer-const` (2):
  real-but-tiny bugs fixed in passing.

**Other Phase 0 work**:
- Capture `npm run check` / `test:unit` / `lint` /
  `playwright` baseline numbers in a `REPORT.md`.
- This `FRONTEND_REFACTOR_PLAN.md` lives at the
  repo root so it's discoverable from the
  refactor-phase commits.
- Commit to `dev` (one commit, "chore(frontend):
  phase 0 — ESLint config + lint baseline fix-up").
  Wait CI green.

### Phase 1: Centralised constants

- Move `POOL_STOPS`, `POOL_SIZE_MAX`, `POOL_SIZE_MIN`,
  `DEFAULT_POOL_INDEX_HOME`, `DEFAULT_POOL_INDEX_FOR_YOU`,
  `SLIDESHOW_PRESETS` into `lib/api/limits.ts`.
- Update `routes/+page.svelte`, `routes/for-you/+page.svelte`,
  `lib/components/AdditionalFilters.svelte` to import
  from limits.
- Add a small unit test for the limits module.
- Commit + push, wait CI.

### Phase 2: API endpoints consolidation

- Add `getSystemStatus`, `getAdminIndexStatus`,
  `getFavoriteCount`, `getDislikeCount` to
  `lib/api/endpoints.ts` (with corresponding Zod
  schemas if the backend's OpenAPI doesn't have them
  — re-run `npm run gen:zod` first).
- Replace the 5 raw `fetch()` sites with the new
  endpoints.
- Add a tiny integration test exercising the new
  endpoints' error handling.
- Commit + push, wait CI.

### Phase 3: Composable — `usePhotoFeedback`

- New `lib/composables/usePhotoFeedback.ts` exporting
  `usePhotoFeedback({ get, set, onLike, onUnlike, onDislike, onUndislike, mutualExclusion })`
  that returns `{ toggleFavorite, toggleDislike, isInFlight }`.
- The composable owns the optimistic-with-rollback
  pattern + the mutual-exclusion rule (pressing Like
  clears Dislike, per the existing pattern).
- Migrate the 3 sites that share the read-state + write-state
  shape: `routes/+page.svelte`, `routes/for-you/+page.svelte`,
  and `routes/photo/[id]/+page.svelte`. The composable
  is callback-driven (get/set) so the per-site state shape
  (list, single photo) doesn't have to change.
- **Lightbox NOT migrated.** The Lightbox has a bespoke
  local state (two separate $state booleans synced from
  `items[idx]` via `lastSyncedIdx`) and fires a parent-owned
  callback rather than calling the API itself. The composable
  assumes a stable id + a per-id state record; forcing it
  onto the Lightbox would either lose the idx-change sync
  semantics or add a wrapper indirection with no real win.
  Documented in the file with a `// Phase 3 note: ...` comment.
- Unit test the composable.
- Commit + push, wait CI.

### Phase 4: Composable — `useAlbumMembership`

- New `lib/composables/useAlbumMembership.ts` exporting
  `useAlbumMembership({ getMemberOf, setMemberOf, getPointId, onAdd, onRemove })`
  that returns `{ toggle, setAll }`.
- The composable owns the optimistic-with-rollback
  add/remove pattern + the SvelteSet identity-preservation
  convention (a new Set on every change, restore the
  prior reference on rollback).
- Migrate the 2 sites that share the toggle-with-rollback
  shape: `Lightbox.svelte` (toggleMembership) and
  `routes/photo/[id]/+page.svelte` (addPhotoToAlbumAction).
  Both also have a `refreshMembership` function that
  re-seeds the Set from the server's album list — these
  now go through `setAll`.
- **ImageContextMenu NOT migrated.** It has a different
  shape: add-only, no toggle, no rollback, no memberOf
  tracking. The current behaviour ("add to album, ignore
  failures, close menu") is intentional. Forcing the
  composable onto it would either add unused state or
  change behaviour. Documented in the file.
- Unit test the composable.
- Commit + push, wait CI.

### Phase 5: Likes / Dislikes page consolidation

- New `lib/composables/usePagedAlbumList.svelte.ts`
  exporting `usePagedAlbumList({ pageSize, fetchPage, onRemove, useIndexMap })`
  that returns `{ items, loading, loadingMore, error, hasMore, indexById, load, loadMore, remove }`.
- The composable owns the load/loadMore/remove pattern
  (loading state, pagination, optimistic-remove with
  rollback). Lives in a `.svelte.ts` file because it
  uses `$state` runes.
- An optional `useIndexMap: true` rebuilds an O(1)
  SvelteMap<id,index> shadow of items on every change.
  Worth it on Likes at 1000+ items; the dislikes
  list is sparser in practice, so it uses a simple
  filter.
- Migrate `routes/albums/likes/+page.svelte` and
  `routes/albums/dislikes/+page.svelte` — both pages
  were 80% identical, with only the endpoint wiring
  and copy strings differing.
- ESLint config: add a parser block for `**/*.svelte.ts`
  / `**/*.svelte.js` so the runes (`$state`, `$derived`)
  parse cleanly.
- Unit test the composable.
- Commit + push, wait CI.

### Phase 6: Lightbox decomposition (largest single change)

- `useSlideshow(items, intervalMs)` → `lib/composables/slideshow.ts`.
- `useKeyboardNav({ ... })` → `lib/composables/keyboardNav.ts`.
- `usePhotoPreloader(items, current, isPlaying)` →
  `lib/composables/photoPreloader.ts`.
- `useBlurhashTint(blurhash)` → `lib/composables/blurhashTint.ts`.
- Each composable is unit-tested in isolation (no
  Svelte mount needed).
- The `Lightbox.svelte` component becomes thinner and
  delegates to the composables.
- E2E `lightbox-crossfade`, `lightbox-infinite-nav`,
  `lightbox-preload`, `lightbox-shortcuts` must keep
  passing.
- Commit + push, wait CI.

### Phase 7: URL ⇄ state helper

- New `lib/state/urlState.ts` with `bindToUrl(state, schema)`
  that takes a Svelte 5 $state object and serialises
  declared fields to/from the URL via `replaceState`.
- Migrate the home page (and any other pages with the
  same pattern, if found).
- Unit test the serializer in isolation.
- Commit + push, wait CI.

### Phase 8: CSS token enforcement

- Walk every component, replace hardcoded colours /
  shadows / spacings with their token equivalents.
- Drop dead `var(--name, fallback)` patterns where the
  fallback is unreachable.
- Add a small custom check (grep-based) that fails CI
  if a new hardcoded value appears.
- Commit + push, wait CI.

### Phase 9: Test organisation + final cleanup

- `frontend/REFACTOR_PLAN.md` final scorecard.
- Any svelte-check warnings that became easy to fix
  in passing (currently 22; mostly unused CSS
  selectors — fix while editing the component).
- Commit + push, wait CI.

## What I'm NOT going to do

- **No behaviour changes.** Every commit preserves the
  public UI surface, the API contract, and the
  user-visible flows.
- **No new features.**
- **No framework migration.** Svelte 5 + SvelteKit 2 is
  current; stays.
- **No generated-code edits.** `types.gen.ts`,
  `schemas.ts`, `openapi.json` are produced by
  `npm run gen:*`.
- **No build / deploy changes.** Vite config, Caddy,
  k8s manifests stay as-is.
- **No new dependencies unless absolutely required.**
  ESLint 9 + svelte plugin + typescript-eslint are
  the only additions Phase 0 brings in.

## Open questions for Isaac

1. **Phase 0's ESLint config**: how strict? Default to
   `eslint:recommended` + svelte/typescript, no
   per-file overrides, fail on warnings? Or start
   permissive (warnings only) and tighten per phase?
2. **Phase 6 (Lightbox decomposition)**: each
   extraction is a separate commit (~5 commits) — is
   that the right granularity, or do you prefer a
   single "Lightbox refactor" commit?
3. **The For-You `POOL_STOPS` is genuinely different
   from the home page's** (6 stops, different
   defaults). Keep them as two named exports
   (`POOL_STOPS_HOME` / `POOL_STOPS_FOR_YOU`) or
   unify? The plan keeps them separate because
   Isaac has commented in the past that
   feature-specific tuning matters.
4. **`/photo/{id}` server loader**: read the
   `+page.ts` file before Phase 0 to decide
   whether it stays as raw `fetch` or moves to a
   `lib/api/server.ts` helper.

## Status

Plan committed to repo, pending go.
