# e2e/ — Playwright E2E Tests

Per `../../AGENTS.md`:

> "A core set of fundamental E2E tests covering main user experience.
> Aim for high-quality testing, non-flaky. A large set of exploratory
> E2E tests, to thoroughly explore the app, discover bugs, etc. These
> tests should be used for exploration and testing, not as criteria
> (not essential to pass)."

This directory splits its tests into two tiers — see the marker
comment at the top of each file (`@tier fundamental` or `@tier exploratory`).
Run commands below show how to execute each tier.

## Current breakdown

30 files, 281 test cases (counts from a `grep -c "^\s*test("` pass; may
drift as tests are added/removed):

| Tier | Files | Cases | CI gate? |
|---|---|---|---|
| Fundamental | 9 | 149 | Yes — must pass to merge to main |
| Exploratory | 9 | 82 | No — failures posted for human triage |
| **Unmarked** | 12 | 50 | Defaults to the full-suite run; needs a tier marker assigned |

## Fundamental — CI gate

A failure means a core user flow is broken in a way users would
immediately hit.

| File | Cases | Intent |
|---|---|---|
| `smoke.test.ts` | 14 | First-pass "does each page render" |
| `full-ux.test.ts` | 14 | Top-level flows: search, lightbox, favorites, similar, For You, Albums |
| `photo-page.test.ts` | 9 | Hero image, sidebar metadata, like toggle, photo navigation |
| `user-journeys.test.ts` | 17 | End-to-end stories (search → photo → similar, like → favorites) |
| `accessibility.test.ts` | 24 | Keyboard navigation, ARIA roles, focus management |
| `navigation-flows.test.ts` | 27 | Back/forward button, route loads, URL state persistence |
| `album-search.test.ts` | 11 | Album Search-button flows |
| `ui-flows.test.ts` | 11 | + New album CRUD, photo page, search composer |
| `photo-context.test.ts` | 22 | Right-click context menu, photo detail page, similar, For You |

## Exploratory — not a gate

Useful for discovering bugs and stress-testing. Failures here are
informative but do not block releases.

| File | Cases | Intent |
|---|---|---|
| `concurrency.test.ts` | 17 | Race conditions, rapid clicks, stress, network resilience |
| `edge-cases.test.ts` | 23 | Special chars, long prompts, only-filename filter, edge cases |
| `features.test.ts` | 17 | API contracts, error mappings, zip download, right-click context menu |
| `backdrop-tint.test.ts` | 6 | Visual styling (frosted backdrop tint) |
| `home-tab-resets-state.test.ts` | 5 | Home-tab-clears-URL-state regression |
| `photo-dimensions.test.ts` | 2 | Source dimensions vs "—" |
| `photo-page-no-indexing-metadata.test.ts` | 2 | "Photo page doesn't show indexing junk" |
| `settings-index.test.ts` | 8 | Settings page + index start/cancel/log |
| `from-scratch.test.ts` | 2 | Boots a clean slate end-to-end |

## Unmarked — no tier assigned yet

These files were added after the tier system was introduced and haven't
been classified. They run in the full-suite invocation but neither gate
merges nor get posted as triage PR comments. Assign a tier (add
`@tier fundamental` or `@tier exploratory` to the file header) when
you next touch one of them.

| File | Cases |
|---|---|
| `blurhash-pool.test.ts` | 2 |
| `cmd-k-search.test.ts` | 3 |
| `lightbox-crossfade.test.ts` | 3 |
| `lightbox-preload.test.ts` | 5 |
| `lightbox-shortcuts.test.ts` | 4 |
| `modal.test.ts` | 7 |
| `photo-page-load.test.ts` | 2 |
| `sample-mode.test.ts` | 6 |
| `slideshow.test.ts` | 11 |
| `thumbnail-sizes.test.ts` | 2 |
| `tile-remove-buttons.test.ts` | 4 |
| `view-transitions.test.ts` | 1 |

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

The unmarked files run in the full-suite job today; once each is
assigned a tier they get the appropriate CI treatment automatically.
