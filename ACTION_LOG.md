# ACTION_LOG.md — image-search

Append-only. One entry per meaningful action.

## 2026-08-05 — Survey

- Isaac asked ("take a look at the repo"). Survey only — no plan/execute.
- Inspected: tree, README, pyproject, branch, diff vs main, env example.
- Findings:
  - Python 3.10+, FastAPI + indexer split, SigLIP2 via open_clip, Qdrant backend.
  - 21 test files under `tests/`, in-memory Qdrant + mock encoder — no GPU needed for CI.
  - Docker compose + Dockerfile for the search app; indexer runs on host.
  - On `feature/perf-review`, 1 commit ahead of main (5f7f32e): excludes near-duplicate
    seeds from dynamic-centroid results. +629/-36 across app/centroids/qdrant_client/test.
  - Static/templates present; `.benchmarks/` exists but is empty.
- Artifacts created: `GOALS.md` (stub; awaiting stated intent), this log.
- Next: wait for direction — happy-state test run? full review? perf numbers?

## 2026-08-05 — Merge feature/perf-review → main

Isaac: "merge into main." Then "let's add something to your MEMORY… feature branch + greenlight… do (3) then (1)."

- **Memory rule added** (MEMORY.md § Git conventions): new features go on `feature/<slug>`; Hyperion never merges to `main` without Isaac's explicit greenlight. Bug fixes / touch-ups: ask.
- **(3) Stash + retry venv:** `git stash -u`, `pip install -e ".[dev]"`, `pytest tests/ -q`. Result: same `10 failed, 435 passed`. Failures are real — not venv-staleness.
- **(1) Fast-forward merge feature/perf-review → main:**
  - `git checkout main` (was up to date with `origin/main`).
  - `git merge --ff-only feature/perf-review` → `b8bc417..5f7f32e`, 4 files / +629/-36.
  - `git push origin main` → `b8bc417..5f7f32e main -> main`. OK.
  - `git branch -d feature/perf-review` (local). OK.
  - `git push origin --delete feature/perf-review`. OK.
- **Post-merge verification:** `pytest tests/ -q` on main → `10 failed, 435 passed` (unchanged — pre-existing).
- **Repo state:** on `main` @ `5f7f32e`, working tree clean except for the untracked Hyperion files (`GOALS.md`, `ACTION_LOG.md`). Remote branches: `main`, `feature/folders-page` (untouched).
- **Open:** 10 pre-existing test failures on `main` (template drift + DB migration fixtures) — not in the merged commit's surface area, untriaged. Action item for follow-up, not a merge blocker.

## 2026-08-05 — Fix pre-existing test failures (in progress)

Isaac: "Fix all as you see fit." Pre-authorised the fix on a non-main branch with discretion on the approach.

- **Branch:** `fix/legacy-db-and-random-template` (off `main @ 5f7f32e`).
- **Root cause analysis:**
  - 8 of 10 failures: `_migrate_images_fts` in `search/index_db.py` was calling `init_from_qdrant(force=True)` for the legacy case. That's correct for production (Qdrant has the data) but destructive in test fixtures that seed SQLite directly to bypass Qdrant. The rebuild DELETEs the seeded rows and scrolls an empty in-memory Qdrant, ending with 0 cached rows.
  - 2 of 10 failures: `search.html` no-query state had an empty `<header class="random-picks-header">`. Test docstring said it should say "Random picks".
  - T3 (grid-sentinel on /random) turned out unnecessary — `random.html` already passes `has_more=True` to the partial, so once data survives, sentinel renders.
- **Fix implemented:**
  - **T1** (`search/index_db.py`): added `schema_meta` table. New `_migrate_images_fts` is one-shot via `fts_v1` flag, non-destructive (DELETE FROM images_fts; INSERT INTO images_fts(rowid, path) SELECT rowid, path FROM images). Operators needing a full Qdrant-driven rebuild still have `POST /api/cache/refresh`.
  - **T2** (`search/templates/search.html`): added `<h2 class="random-picks-title">Random picks</h2>` inside the previously-empty header.
- **Test results:** 17/17 targeted (the 10 previously failing + 7 sibling random-API tests), 445/445 full suite. Was 435/10 before. No regressions.
- **Committed:** `7f41a95` (on `fix/legacy-db-and-random-template`).
- **Pushed:** `git push -u origin fix/legacy-db-and-random-template` — branch + upstream tracking live on `origin`.
- **Sentinel review:** in flight. Child session `5ff9ba26-e321-40f3-bb09-42cb8d0fccd9` / run `bb9fdb30-4ffe-4d70-8efa-8997ae27f5b1`. Awaiting verdict. On PASS, ask Isaac for merge greenlight per the new MEMORY git rule.
- **Sentinel review:** **PASS** (4m27s, 49.5k tokens). Verdict: 445/445 green; the 10 previously-failing tests are in the green set; diff exactly the two files in PLAN.md; no public-API / schema-contract changes; no Critical-severity issues. New FTS backfill is correct, idempotent via `schema_meta.fts_v1`, strictly cheaper than the prior `init_from_qdrant(force=True)` path. Full review at `REVIEW.md` (per-AC PASS, diff review by file: correctness / edge cases / security / performance / error handling / breaking).
- **Next:** await Isaac's merge greenlight per the new MEMORY git rule. Branch ready to fast-forward into `main @ 5f7f32e` and delete locally + remote.

## 2026-08-05 — Merge fix/legacy-db-and-random-template → main

Isaac: "yes" (merge greenlight at 18:28 EDT).

- `git checkout main` — up to date with `origin/main @ 5f7f32e`.
- `git merge --ff-only fix/legacy-db-and-random-template` → `5f7f32e..7f41a95` (2 files / +93/-59). No merge commit (linear history).
- `git push origin main` → `5f7f32e..7f41a95 main -> main`.
- `git branch -d fix/legacy-db-and-random-template` (local). `git push origin --delete fix/legacy-db-and-random-template` (remote). OK.
- **Post-merge smoke test:** `pytest tests/ -q` → 445/0 (unchanged from pre-merge; fast-forward doesn't change tree content).
- **Final state:** `main @ 7f41a95` on local + remote; working tree clean except for untracked Hyperion state files (`PLAN.md`, `REVIEW.md`, `GOALS.md`, `ACTION_LOG.md`). Branches: `main` (current), `feature/folders-page` (untouched).
- **Project state:** all 10 pre-existing test failures resolved; suite is green; the fix landed on main. The new MEMORY git rule is in force and worked as designed (feature branch → greenlight → merge → branch cleanup).

## 2026-08-05 — Delete `feature/folders-page` + deeper design review

Isaac: "the albums branch didn't work well. delete. Look at the design of the main branch deeper though. Many of those are surface-level observations."

- **Branch delete:** `feature/folders-page` (which Isaac referred to as "the albums branch") deleted locally + remote. I had previously misnamed it in the survey ("browse by directory tree" assumption) — the branch was about albums, not folders. Branches remaining: `main` only.
- **Deeper design review delivered** (in-channel). Read the rest of the codebase to back the claims: `centroids.py`, `discover.py`, `qdrant_client.py`, `upsert.py`, `models.py`, `text_encoder.py`, `diversity.py`, `indexer/indexer.py`. Key findings:
  1. Dual-store sync seam (Qdrant + SQLite + indexer cache as three implicit sources of truth) — biggest design tension.
  2. `create_app` is a 700-line factory with service-locator globals and nested helpers unreachable for unit tests.
  3. Operational constants (`MAX_RESULTS_TOTAL`, the discover burst timeline, etc.) are hardcoded module globals instead of `Config`-driven.
  4. Two MMR implementations (`diversity.mmr_rerank` pure-Python vs `centroids._mmr_select` numpy) — unjustified split.
  5. `QdrantSearch.random_window` is the discover cold-start bottleneck (full collection scroll per call in the fallback path).
  6. The implicit shared contract with `isaac-image-scoring` (letterbox preprocessing, sha512 mock embedder) is comment-only with no shared import.
  7. Smaller: copy-paste in QdrantSearch filter-building, resumeless `prune_missing`, docstring drift in `SearchResult.id`, `ErrorResponse.code` should be `Literal`, `pick_random` vs `pick_random_rows` drift risk, dynamic-centroid compute race.
- **Recommended priority (sent in-channel):** (1) SCHEMA.md / shared metadata package, (2) lift `create_app`, (3) constants → Config, (4) unify MMR, (5) backup story for user state. (1) and (2) are the highest leverage; (3)-(5) are quick wins. Awaiting Isaac's call on which to plan.
- **Not yet read** (no opinion): `indexer/vision_encoder.py`, `indexer/image_loader.py`, `indexer/scan.py`, `indexer/cache.py`, `indexer/heal.py`, `search/config.py`, `search/image_resolver.py`, `search/qdrant_url.py`. The claims above are based on what I did read.

## 2026-08-05 — `refactor/constants-to-config` (#3 from deeper review)

Isaac: "do all" at 18:56 EDT. Did #3 first (constants → Config) since it's the foundation for #2; #2 (lift `create_app`) is a multi-PR refactor queued for after this lands.

- **Branch:** `refactor/constants-to-config` (off `main @ 7f41a95`).
- **PLAN.md** written with 5 tasks (T1 config.py, T2 app.py, T3 discover.py, T4 tests, T5 .env.example) + acceptance criteria.
- **Implementation:**
  - `search/config.py`: 13 new Config fields + `_float` helper + `load()` plumbing for 11 env vars (valid_views + default_view are not env-driven; closed enum kept on Config for testability).
  - `search/app.py`: 7 module-level constants deleted. References sed-substituted to `_cfg.foo` (routes already closed over `_cfg`). Re-added `logger = logging.getLogger(__name__)` after an over-eager sed had deleted it.
  - `search/discover.py`: 6 module-level constants deleted. Added `DiscoverOptions` dataclass (frozen, snapshots per-session values). `_next_pair` reads from `session.opts.foo`. `_gc_expired` takes `ttl_seconds` as a parameter. `submit_pick` and `start_session` take `opts` as a kwarg. `DiscoverOptions.from_config(cfg)` classmethod for app.py + tests.
  - `tests/test_discover.py`: helpers `_seed_rounds`, `_burst_size`, `_recommend_overfetch`, `_mmr_pool_size` read from `app_mod.get_cfg()` lazily. The one direct call to `discover.start_session` (bypassing the HTTP route) constructs `DiscoverOptions` explicitly.
  - `.env.example`: 11 new env var entries with descriptions matching the prior module-level comments.
- **Mid-flight issues:** `cfg` vs `_cfg` in app.py routes (sed produced `cfg.foo`; fixed to `_cfg.foo`). Missing `logger` line (sed range was 65-108; logger was at 65; re-added). Direct `discover.start_session(qdrant, index_db)` call in tests bypassed the route's `DiscoverOptions` plumbing (fixed to use `DiscoverOptions.from_config(app_mod.get_cfg())`).
- **Test results:** 445 passed, 0 failed.
- **Committed:** `298dab0` (5 files / +335/-222).
- **Sentinel review:** **PASS** (7m24s, 94.2k tokens). 3 Low-severity observations: (a) `submit_pick`'s `opts` parameter is currently unused by callers (defensive, fine); (b) `_coerce_view` now depends on `_cfg` being set by the time it's called (true; the route registration flow guarantees this); (c) one test (`test_seed_phase_uses_index_db_pick_unseen`) has hardcoded DiscoverOptions literals that match the defaults (could read from cfg, but acceptable for a unit test that doesn't care about specific config values). No Critical/High issues. Full review at `REVIEW.md`.
- **Next:** await Isaac's merge greenlight per the new MEMORY git rule. Then start `#2 lift create_app` as a multi-PR effort.

## 2026-08-05 — T1: CI build workflow (Track B)

- **Task:** T1 of PLAN.md Track B. Add GH Actions workflow that builds and pushes `:dev` + `:sha-<short>` to `ghcr.io/isaaclb98/image-search` on push to `main` / `feature/**` / `fix/**` / `refactor/**`.
- **Branch:** `feature/ci-build-dev` (off `main @ 63385e8`).
- **File:** `.github/workflows/build-dev.yaml` (53 lines). buildx, GHA layer cache (`type=gha,mode=max`), `permissions: contents:read, packages:write`, OCI labels.
- **Lint:** `python3 yaml.safe_load` parsed clean. Note: PyYAML interprets a bare `on:` key as YAML 1.1 boolean `True` (known quirk) — GH Actions parses it correctly as the trigger key.
- **Commit:** `b893738`. Message per MEMORY (no AI attribution). Author = global git identity.
- **Push:** `git push -u origin feature/ci-build-dev` landed; `git ls-remote origin feature/ci-build-dev` confirms `b893738bbaea7e7478be1b6e1f9dc485aec2d75e` at the ref.
- **Scope guard:** 1 file, +53 lines. ACTION_LOG / GOALS / PLAN / REVIEW remain untracked (internal project state, not source).
- **Acceptance:**
  - AC-B1 ✓: workflow file at the expected path, parses, pushed.
  - AC-B2 ⏸: requires an actual push to a tracked branch to trigger. Will land when T2 is in flight and we need a fresh `:dev`.
- **Next:** ready to proceed to T2 (manifests in `gitops/clusters/home/apps/image-search/`). Awaiting Isaac's call: proceed, or Sentinel review first.

## 2026-08-05 22:27 EDT — feature/design-system planning

- Isaac asked: "what could we steal from PhotoPrism for image-search?"
- Discussion refined to UI only, then to "polished, professional UI" direction.
- Recommendation: design-system foundation as branch 1; lightbox + LQIP, hover-stack, grid consolidation, keyboard shortcuts as branches 2-5.
- Stack chosen: FastAPI + Jinja + Tailwind standalone CLI + DaisyUI + Alpine.js. No Node, no React.
- Plan written: `PLAN.md` (overwrote stale `fix/dual-store-cleanup` content — was untracked). `GOALS.md` updated with new direction.
- Status: awaiting Isaac's greenlight to cut `feature/design-system` and start execution.

## 2026-08-05 22:33 EDT — branch scope revised

- Isaac: "each feature does not need to be a completely separate branch. Just one feature branch to capture them all."
- Consolidated branches 1-5 (design-system, lightbox-and-lqip, hover-stack, grid-consolidation, keyboard-shortcuts) into single branch `feature/polished-ui`.
- PLAN.md rewritten: 4 phases (foundation / indexer / interactive / validation), 15 tasks, 11 acceptance criteria. Branch name: `feature/polished-ui`.
- GOALS.md updated to reflect single-branch decision.
- Status: plan ready, awaiting greenlight to cut branch and start execution.

## 2026-08-05 22:37 EDT — branch split confirmed

- Isaac: "Okay, that sounds right." Confirms 2-branch split.
- `feature/polished-ui-foundation` — T1-T8 (Tailwind, tokens, themes, typography, header, grid, states, collection cards) + T9 (blurhash index + payload + `--reblurhash` backfill) + T11/T12 (tests + CI for foundation). LQIP ships data-only; client decoder deferred to branch 2.
- `feature/polished-ui-interactive` — T10-T13 (lightbox, hover-stack, grid consolidation, shortcuts) + client blurhash decoder. Depends on branch 1.
- PLAN.md rewritten for branch 1 only. Branch 2 sketched in Out-of-scope.
- Status: plan ready, awaiting greenlight to cut `feature/polished-ui-foundation`.

## 2026-08-05 23:01 EDT — T3 (theme system) complete

- T1 (foundation) committed: `de6356c`. Tailwind v4.3.3 + DaisyUI v5.7.16 vendored, design tokens, custom light/dark re-skins, base.html → app.css, blurhash dep added.
- T3 (theme system) committed. FOUC-prevention inline script in <head>, theme toggle in header with sun/moon SVG, Alpine.js 3.14.1 via CDN, data-theme="light" default on <html>.
- 445/445 tests green after both commits.
- T2 (design tokens) + T4 (typography) effectively done as part of T1's input.css — marked complete.
- Branch status: `feature/polished-ui-foundation` at 2 commits ahead of main. Remaining: T5-T8 (template migration), T9 (blurhash index + payload + backfill), T10 (blurhash thumb macro), T11 (tests), T12 (CI + docs).

## 2026-08-06 23:30 EDT — complete UI coherence + interactive pass

- Isaac authorized the full UI pass on `feature/polished-ui-tasks`.
- Unified legacy CSS variables with the light/dark DaisyUI tokens, added responsive mobile navigation, rebuilt the search surface around a primary query plus collapsible filters, and standardized page treatments across photo, album, discovery, random, favorites, saved, and centroid views.
- Added shared SVG icon macro, canvas BlurHash decoder, photo-card hydration, lightbox with focus restoration/trapping, clickable and keyboard navigation, quick favorite and album actions, keyboard help, reduced-motion styling, discovery-pick gallery hydration, live favorites-page state updates, and static asset cache-bust `27`.
- Added `tests/test_ui_interactive.py` and updated README/PLAN documentation.
- Verification: `./bin/tailwindcss ...` succeeded; `.venv/bin/pytest -q` → **488 passed, 2 warnings**; all JS files passed `node --check`; `git diff --check` passed; Playwright seeded-Qdrant smoke passed for 12 result tiles, BlurHash canvases, lightbox next/previous/Escape, shortcut help, and favorite POST with no page errors.
- `ruff check search indexer tests` still reports 55 pre-existing repository lint findings; no lint config or unrelated cleanup was applied.
- Commits `2096f2b` and `ba30f6b` are pushed to `origin/feature/polished-ui-tasks`; merge approval was pending at feature completion.
- Sentinel final review: **PASS** after repairing clickable lightbox arrows, x-cloak, mobile lightbox scrolling, favorite/discovery state synchronization, and favorites-page shell updates.

## 2026-08-07 — merge approved

- Isaac explicitly approved merging `feature/polished-ui-tasks` into `main`.
- The merge resolution restored the complete reviewed feature tree, including the inherited BlurHash indexer, CI workflow, and state/theme regression tests that `main`'s prior revert had removed.
- Merge-result verification: Tailwind build, JavaScript syntax checks, Python compileall, `git diff --check`, and **488 tests passed with 2 known warnings**.

## 2026-08-07 — Search Diversity implementation authorized

- Isaac authorized implementation of the search-only Diversity overhaul.
- Scope: stable pre-pagination ranking, duplicate awareness, relevance-preserving
  coverage, explicit Diversity strength, API metadata, and focused verification.
- Explicit exclusion: Discovery is not part of this change and must remain
  behaviorally untouched.
- Created feature branch `feature/search-diversity` from `main`.
- Preserved pre-existing untracked `REVIEW.md`.

## 2026-08-07 — Search Diversity verification

- Focused search/indexer/Diversity tests passed; final full suite reached
  **503 passed, 2 warnings**.
- Tailwind output was rebuilt and confirmed current; Python compilation,
  JavaScript syntax checks, and `git diff --check` passed.
- Discovery scope guard passed: `search/discover.py` and
  `search/static/js/discover.js` have no diff from `main`.
- Sentinel review was invoked twice but both delegated review processes stayed
  running without producing a verdict and were shut down. No Sentinel PASS is
  claimed; the residual review limitation is reported at handoff.

## 2026-08-07 — Search Diversity depth controls authorized

- Isaac authorized separate Diversity strength and candidate-pool depth
  controls after observing that High remained insufficiently diverse.
- Implemented `diversity_depth=auto|500|1000|2000|5000`; Auto maps to 500,
  1,000, and 2,000 candidates for Low, Balanced, and High respectively.
- High now uses a stronger ranking weight and wider relevance allowance; depth
  is included in API metadata, URL state, cache keys, and stable pagination.
- Discovery remains explicitly out of scope.

## 2026-08-07 — Search Diversity depth review repairs

- Sentinel review returned `NEEDS_WORK` with three P2 findings: photo
  back-links dropped Diversity state, the client-side legacy URL alias could
  override explicit `diversity=off`, and a configurable Auto floor could make
  Low and Balanced resolve to the same pool depth.
- Preserved Diversity mode/depth through `/photo/{id}` back-links and added a
  route regression test.
- Made explicit `diversity=off` win over the legacy `diverse=true` alias in the
  URL reader.
- Removed the obsolete configurable Auto floor so Auto always resolves to the
  distinct 500 / 1,000 / 2,000 mode depths; explicit depths remain capped by
  `DIVERSITY_MAX_CANDIDATE_POOL_SIZE`.
