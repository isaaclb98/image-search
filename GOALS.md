# GOALS.md — image-search

## Stated intent (Isaac)

- **Git workflow (added 2026-08-05):** New features go on a feature branch. Hyperion never merges to main without Isaac's explicit greenlight. Bug fixes / touch-ups: ask.
- **Deploy to cluster (added 2026-08-05):** Isaac is on a headless server; can't test locally. Image-search needs a live cluster deployment via ArgoCD. Manifests in `gitops` from day one. ArgoCD Sync is manual (no image-updater controller). One instance only — shares the existing `images` Qdrant collection.
- **Polished, professional UI (added 2026-08-05):** "I want a more polished, professional UI." Direction: lift from project-grade to product-grade via a real design system — design tokens, themed components, photo-first chrome, designed states, LQIP, lightbox, hover-stack, grid consolidation, keyboard shortcuts. Stack decision: stay on FastAPI + Jinja; bring in Tailwind standalone CLI + DaisyUI; Alpine.js for theme + shortcuts + hover-stack; HTMX for lightbox side panel; small vendored blurhash decoder. **Two branches, not five — Isaac: "maybe you had it right before," confirmed 22:37 EDT.** Plan written to `PLAN.md` under `feature/polished-ui-foundation`. Branch 2 (`feature/polished-ui-interactive`) is sketched in PLAN.md's Out-of-scope section. Awaiting Isaac's greenlight to start execution.

This conversation: "Hello. Take a look at my image-search repo." → "merge into main." → "deploy to k8s" — 2026-08-05

## Inferred direction (from repo state)

- Self-hosted semantic image search over a personal photo library.
- Read path: FastAPI app on port 8000. Write path: CLI indexer.
- SigLIP2 (via `open_clip`) embeddings → Qdrant.
- Recent work was on a `feature/perf-review` branch (1 commit, since merged to main 2026-08-05):
  centroid-search perf — exclude near-duplicate seeds from dynamic-centroid results.
- Active branch: `fix/legacy-db-and-random-template` (off main 2026-08-05) — one-shot FTS backfill + "Random picks" header; addresses 10 pre-existing test failures on main. Merged to main 2026-08-05; branch deleted.
- **Deployment target: cluster** (added 2026-08-05). Image-search moves from local docker-compose to a k8s dev deployment at `image-search-dev.aizaku.ca`. CI builds `:dev` on push; ArgoCD Sync is manual.

## Non-negotiables (per global conventions)

- Public OSS on GitHub → no AI attribution, no full name, no personal info.
- Prefer existing OSS / no reinvention.
- One remote only (GitHub for this repo).

## Status

- `main @ 5f7f32e` — clean.
- `fix/legacy-db-and-random-template` — merged to main (`main @ 7f41a95`), branch deleted locally + remote. Post-merge smoke test: 445/445 green.
- `refactor/constants-to-config` — Sentinel PASS (7m24s, 3 Low observations). Branch + commit (`298dab0`) + push done; review written to `REVIEW.md`. Awaiting Isaac's merge greenlight. After this lands, `#2 lift create_app` is the next branch.
- `feature/folders-page` — deleted 2026-08-05 at Isaac's direction ("didn't work well"). Local + remote gone.
- **Track B (deploy infra):** Plan written to `PLAN.md`. Pending Isaac's greenlight to start execution. Open: NFS export path, ArgoCD Application placement pattern.
