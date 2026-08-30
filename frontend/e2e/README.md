# e2e/ — Playwright E2E Tests

Per `../../AGENTS.md`:

> "A core set of fundamental E2E tests covering main user experience.
> Aim for high-quality testing, non-flaky. A large set of exploratory
> E2E tests, to thoroughly test the app, discover bugs, etc. These
> tests should be used for exploration and testing, not as criteria
> (not essential to pass)."

This directory splits its tests into two tiers — see the marker
comment at the top of each file.

## Fundamental (~125 tests, 9 files) — CI gate

A failure means a core user flow is broken in a way users would
immediately hit. These tests gate releases.

| File | Intent |
|---|---|
| `smoke.test.ts` | First-pass "does each page render" |
| `full-ux.test.ts` | Top-level flows: search, lightbox, favorites, similar, For You, Albums |
| `photo-page.test.ts` | Hero image, sidebar metadata, like toggle, photo navigation |
| `user-journeys.test.ts` | End-to-end stories (search → photo → similar, like → favorites) |
| `accessibility.test.ts` | Keyboard navigation, ARIA roles, focus management |
| `navigation-flows.test.ts` | Back/forward button, route loads, URL state persistence |
| `album-search.test.ts` | Album Search-button flows |
| `ui-flows.test.ts` | + New album CRUD, photo page, search composer |
| `photo-context.test.ts` | Right-click context menu, photo detail page, similar, For You |

## Exploratory (~83 tests, 9 files) — not a gate

Useful for discovering bugs and stress-testing. Failures here are
informative but do not block releases.

| File | Intent |
|---|---|
| `concurrency.test.ts` | Race conditions, rapid clicks, stress, network resilience |
| `edge-cases.test.ts` | Special chars, long prompts, only-filename filter, edge cases |
| `features.test.ts` | API contracts, error mappings, zip download, right-click context menu |
| `from-scratch.test.ts` | Fresh-install look + cancel-mid-run index |
| `backdrop-tint.test.ts` | Visual styling (frosted backdrop tint) |
| `home-tab-resets-state.test.ts` | Home-tab-clears-URL-state regression |
| `photo-dimensions.test.ts` | Source dimensions vs "—" |
| `photo-page-no-indexing-metadata.test.ts` | "Photo page doesn't show indexing junk" |
| `settings-index.test.ts` | Settings page + index start/cancel/log |

## Running the suites

Run everything (current default):

```bash
cd ~/projects/image-search/frontend
PLAYWRIGHT_BASE_URL=http://127.0.0.1:18000 \
  node_modules/.bin/playwright test
```

Run only fundamental:

```bash
cd ~/projects/image-search/frontend
PLAYWRIGHT_BASE_URL=http://127.0.0.1:18000 \
  node_modules/.bin/playwright test \
  smoke.test.ts full-ux.test.ts photo-page.test.ts user-journeys.test.ts \
  accessibility.test.ts navigation-flows.test.ts album-search.test.ts \
  ui-flows.test.ts photo-context.test.ts
```

Run only exploratory:

```bash
cd ~/projects/image-search/frontend
PLAYWRIGHT_BASE_URL=http://127.0.0.1:18000 \
  node_modules/.bin/playwright test \
  concurrency.test.ts edge-cases.test.ts features.test.ts \
  from-scratch.test.ts backdrop-tint.test.ts home-tab-resets-state.test.ts \
  photo-dimensions.test.ts photo-page-no-indexing-metadata.test.ts \
  settings-index.test.ts
```

## CI recommendation

Two jobs:

1. **Fundamental job** — must pass. Gate merge to main.
2. **Exploratory job** — allowed to fail. Results posted as a PR
   comment for human review (failures here are bugs to triage, not
   gates).

See `../../.hermes/plans/2026-08-30_123000-e2e-tier-organisation.md`
for the full rationale.