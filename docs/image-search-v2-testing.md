# image-search v2 — Testing Strategy

## Core principle

Test **the risk**, not the code. The risk in v2 is concentrated in two places:

1. **The API contract breaking** — FastAPI schema → TypeScript types → frontend assumes shape X, backend returns shape Y. This is the #1 way SvelteKit + FastAPI projects rot.
2. **The reusable grid + lightbox** — every page (search, random, for-you, home, albums) consumes them. Test them *once, well* and 5 pages inherit the coverage.

Backend logic (search math, centroids, albums) is already covered by the existing pytest suite — it stays, and we are **not** rewriting those tests.

## The four layers

### Layer 1 — Backend: pytest + httpx (keep, don't rewrite)

The existing suite stays as-is. One addition: **OpenAPI stability tests** — every new/changed endpoint gets a test asserting the schema fragment (openapi.json) contains exactly the params/shapes the frontend depends on. If someone changes `limit` from int to string, the test fails *before* the frontend ever sees it.

### Layer 2 — Contract: OpenAPI as the backbone (the new, important piece)

- `openapi-typescript` generates `.d.ts` types from `openapi.json` — committed to the repo, so type drift is visible in code review.
- **Zod schemas generated from the same OpenAPI** (via `openapi-zod-client` or similar). Two uses: validate API responses in **dev mode** (catches drift live while building) and in tests. Typegen gives compile-time safety; zod gives runtime safety. Compile-time alone lies when the backend drifts at runtime.

### Layer 3 — Component tests: Vitest + Svelte Testing Library

- Test primitives in isolation with mocked fetch: grid (renders N columns, infinite scroll sentinel), lightbox (open, keyboard left/right nav, close), prompt chips (add/remove, pos/neg toggle, input cleared).
- Fast (<1s each), no browser. This is where the grid's *reusability contract* gets locked — if a page breaks the grid, its component test catches it, not an e2e suite.

### Layer 4 — E2E: Playwright, thin on purpose

One test per critical user flow, against the real stack (SvelteKit + FastAPI + seeded Qdrant):

- Search: type prompt → add chip → Search → grid → infinite scroll
- Lightbox: click → left/right nav → close
- **New-tab: middle-click/⌘-click a photo → standalone photo page loads** (verifies tab behaviour natively)
- Like → appears in Albums → Likes album; Dislike → Dislikes album
- Album CRUD + "Search with album" (centroid)
- For You: like a few photos → recommendations change

Target: ~8–10 e2e tests. They are slow and flaky-prone; keep them precious.

## Test data

Reuse the demo-data seeder concept: a `--seed` command producing a fixed, deterministic library (photos with known filenames/prompts) so searches are assertable ("search 'mountain' returns exactly these 4").

- E2E runs against seeded data.
- Component tests use mocked fetch.

## Decisions

| Decision | Pick |
|---|---|
| Runtime zod validation | Yes, dev-mode only, zero prod overhead |
| E2E scope | Critical flows only (~8–10); the rest lives in component tests |
| CI | GitHub Actions on every push: backend pytest + frontend vitest + type-check; e2e on-demand |
