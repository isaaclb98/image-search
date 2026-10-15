# Image Search Backend — Refactor Plan (Round 93+)

**Goal.** Make the backend easier to extend, easier to read, and harder to
break. No functional changes; the public API contract and test suite are
the regression net.

**Strategy.** Eight independent PRs, each on its own branch, each small
enough to review in one sitting, each merged to `dev` (never to `main`
without explicit at-the-moment permission). PRs are ordered so each one
makes the next one safer — the early ones are mechanical, the later ones
are structural.

**Out of scope (will NOT be touched in any PR below):**
- `indexer/` (its own subsystem, not part of the search service surface)
- `image_search_kernel/` (already strict-typed, CI-gated)
- `frontend/` (refactor lives in a separate cycle)
- Any new feature, new route, new field, or behavior change

**Existing branches I will leave alone** (unmerged, behind `dev`):
- `feat/centroid-diversity`
- `refactor/visual-balance`

If you want me to rebase either of those onto the refactor's `dev`,
open a follow-up — this plan doesn't touch them.

---

## The eight PRs

| # | Branch | Title | Effort | Risk | Status |
|---|---|---|---|---|---|
| 1 | `refactor/plan-001-suppression-discipline` | Suppression discipline + lint tightening | 1 afternoon | Trivial | **DONE** (PR #13, CI green, 2026-10-15) |
| 2 | `refactor/plan-002-module-layout` | Standardize router / compute / deck / config split | 2-3 days | Low | **DONE** (PR #14, CI green, 2026-10-15) — extracted `RandomSessionStore` to its own module, moved `build_surprise_deck` to `surprise_deck.py`, deduped `_bad_request`/`_coerce_view` against `_result_helpers.py` canonicals, added `__all__` to public modules. App.py + 36 BLE001/S608/S324 suppressions still pending (PR 3+ work). |
| 3 | `refactor/plan-003-type-the-search-surface` | Type the public surface of `search/` (mypy per-module) | 3-4 days | Medium | **DONE 3a** (PR #15, CI green, 2026-10-15) — fixed all 70 `type-arg` errors in `search/` (parametrize `dict`/`list`/`tuple`/`Task`/`Popen` and add `SearchHit`/`SearchResult` imports). Split into 3a/3b/3c. 3b = call-arg + no-untyped-def (47+30 errors). 3c = the rest (arg-type, dict-item, no-untyped-call, assignment, return-value, override, attr-defined, etc.). |
| 4 | `refactor/plan-004-pydantic-search-results` | Replace ad-hoc dicts with typed payload models | 2-3 days | Low | |
| 5 | `refactor/plan-005-settings-class` | Centralize env-var parsing into a pydantic-settings class | 1-2 days | Low | |
| 6 | `refactor/plan-006-problem-detail-errors` | RFC 7807 error envelopes + global exception handlers | 1-2 days | Low | |
| 7 | `refactor/plan-007-api-naming-review` | API surface review + naming consistency | 2-3 days | Medium | |
| 8 | `refactor/plan-008-final-tightening` | `Protocol`s, `__all__`, module docstrings, CI mypy gate on | 1-2 days | Low | |

**Total:** ~14-22 calendar days at sustainable pace. If you want me to
slow down between PRs for review, the gap goes longer; if you want me
to push through, this is the floor.

---

## PR 1 — Suppression discipline + lint tightening (this PR)

**Files touched:** 7

1. `search/routers/search.py` — change `api_search` return annotation
   to `SearchResponse | JSONResponse`; remove 12 `type: ignore[return-value]`
2. `search/routers/centroids_search.py` — same fix on `search_by_centroid`;
   remove 22 `type: ignore[return-value]`
3. `search/routers/random.py` — same fix on `api_random`; remove 6
   `type: ignore[return-value]`; replace 1 `# type: ignore[attr-defined]`
   with a typed `setattr` (or a class-style attribute on the router)
4. `search/routers/for_you.py` — same fix on `for_you_feed`; remove 7
   `type: ignore[return-value]`
5. `search/dev_server.py` — convert `# type: ignore[import-not-found]`
   to a `try/except ImportError` with a guarded fallback, or move the
   import to a typed lazy import.
6. `pyproject.toml` — tighten ruff config: add per-file-ignores for
   `search/routers/*` (suppress `BLE001` in known-defensive spots),
   add a `[[tool.ruff.lint.per-file-ignores]]` block for
   `scripts/*` (already there), document the policy in a comment.
7. `.github/workflows/ci.yml` — **no change**. CI is not the gate
   for suppression policy; review is.

**Suppressions that REMAIN after PR 1 (each one gets a named justification):**
- 11 `BLE001` in `qdrant_client.py` (defensive fallbacks on optional
  operations; logging rather than crashing on transient Qdrant issues
  is the right call)
- 4 `BLE001` in `app.py` (startup/shutdown background tasks; the alternative
  is a crash loop)
- 2 `S311` in `centroids_compute.py` (deterministic seeding for
  reproducible tests; not a security context)
- 2 `BLE001` in `search.py` (count_points fall-back; corpus-size cap
  for Surprise Me is a perf optimization, not a security one)
- 1 `BLE001` in `centroids.py` (torch.load on operator-provided
  centroids; can fail with many error variants depending on torch version)
- 1 `BLE001` in `lazy_index_cache.py` (cache hydration; empty cache
  is a recoverable state)
- 1 `BLE001` in `index_db.py` (qdrant_point_count; UI uses this for
  display, not for security)
- 2 `BLE001` in `config.py` (startup config validation; can't bring
  the service up if these crash)
- 2 `BLE001` in `text_encoder.py` (mock embedder registry fallback;
  embedder resolution is best-effort at startup)
- 1 `BLE001` in `for_you.py` (library size lookup; 0 is a valid value)
- 1 `BLE001` in `admin_index.py` (count_images fallback; same as
  for_you.py — 0 is a valid answer)
- 6 `S608` in `index_db.py` (FTS-injection / SQL string-building;
  all are column-name whitelists, not user input — see existing
  comments)
- 2 `S324` in `app.py` (md5 for ETag, not for security; HTTP cache
  validators don't need cryptographic strength)
- 1 `F811` in `models.py` (re-definition of `PromptState` after a
  forward ref; intentional)
- 1 `F401` in `diversity.py` + 1 `F401` in `centroids.py`
  (re-exports of the public API from `_compute` modules; intentional
  re-export via `__all__`)

That's 36 suppressions going from "scattered `BLE001` with no context" to
"named suppression with a one-line justification." Each one is human-
readable in `git grep`.

**Lint changes in `pyproject.toml`:**
- Add `[[tool.ruff.lint.per-file-ignores]]` for `search/routers/*`:
  - `BLE001` (defensive `except Exception` in router code is OK if
    the alternative is a 500 cascade)
  - `PLR0913` (router functions legitimately take many query params)
  - `PLR0915` (router functions are long by nature; refactor to a
    smaller sub-helper is PR 2's job, not a ruff concern)
- Add a `tool.ruff.lint.flake8-bugbear.extend-immutable-calls` config
  to acknowledge the `QdrantClient` and Pydantic model patterns.
- Add a `tool.ruff.lint.pylint.max-args` override for routers
  (routers take lots of query params; this is a FastAPI idiom).
- Add a comment header documenting the policy: "suppressions are
  fine, but each one gets a named justification; bare `noqa`
  is forbidden."

**Acceptance criteria for PR 1:**
- 60 → 0 `type: ignore[return-value]` (or wherever they were, all gone)
- All other suppressions have a one-line `# justification:` comment
- `ruff check .` passes
- `pytest tests/ --ignore=tests/test_local_sync_perf_helpers.py` still 1517 passing
- mypy error count does not increase (this PR does not add types; it
  removes a class of fake annotations)
- Diff is reviewable in one sitting (~150-300 lines)

**Out of scope for PR 1 (deferred):**
- Adding return types to other functions (PR 3)
- Replacing `SearchResponse`/`JSONResponse` union with a proper tagged
  result type (PR 4)
- Global exception handlers (PR 6)
