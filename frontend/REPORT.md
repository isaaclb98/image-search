# Frontend Refactor — Baseline Report

Captured 2026-10-16 at the start of the frontend refactor
sweep. This file documents the *before* state so subsequent
phases can measure their impact against it. Update the
"After" column at the end of each phase.

## Quality gates

| Gate | Command | Before |
|---|---|---|
| TypeScript + Svelte | `npm run check` | 0 errors, 22 warnings (pre-existing) |
| Unit tests | `npm run test:unit` | 6 files, 72 tests, all pass |
| E2E tests | `npm run test:e2e` | 32 test files, 5,706 lines (Playwright) |
| Lint | `npm run lint` | **broken** — `eslint.config.js` missing |
| Backend API | CI green on `main` | mypy 0 errors in `search/`, 1521 tests pass |

## Codebase

- 11,717 lines in `src/` (excluding generated `types.gen.ts`)
- 9 routes, 24 components
- 6 unit-test files, 32 Playwright e2e files
- 2,360 lines of generated `types.gen.ts` (not edited)
- 192 lines of generated `schemas.ts` (not edited)
- 293 lines of design tokens in `lib/styles/tokens.css`

## First lint pass (after adding ESLint config)

89 problems — see `FRONTEND_REFACTOR_PLAN.md` § "Phase 0"
for the breakdown. All fixed in Phase 0 itself.

## Architecture observations

(Read while planning — informs Phases 1-9.)

- **API layer well-factored**: `lib/api/endpoints.ts` is
  imported by 20 sites. `client.ts` (transport),
  `schemas.ts` (Zod), `types.gen.ts` (OpenAPI compile-time),
  `limits.ts` (constants). Five `fetch()` calls bypass
  this layer (Phase 2 target).
- **Pure-logic modules**: `lightbox-pagination.ts`,
  `dropdown-layout.ts`, `blurhash-bg.ts` — all unit-tested.
  This is the established extraction pattern.
- **Likes and Dislikes pages are 80% identical** (Phase 5).
- **Like/Dislike toggle reimplemented in 4 sites** (Phase 3).
- **"Add to album" reimplemented in 3 sites** (Phase 4).
- **Lightbox is 1013 lines, 70% CSS/docs**. Script has 4
  cleanly extractable composables: slideshow, keyboard nav,
  photo preloader, blurhash tint (Phase 6).
- **`POOL_STOPS` duplicated** between home +
  AdditionalFilters, and **diverged** in for-you (Phase 1).
- **Some hardcoded CSS values** in PhotoTile + PromptChips
  exactly match existing tokens (Phase 8).

## Notes

- Phase 0 ran into one ESLint rule that was a genuine false
  positive: `svelte/no-navigation-without-resolve` fires on
  primitives that render anchor tags (Button, ActionButton,
  PhotoTile, TopBar, ForYouRow) because the rule can't see
  the parent's intent. The fix is a per-file override in
  `eslint.config.js` for those primitives; the rule still
  applies at the route level where the SvelteKit router
  context is available.
