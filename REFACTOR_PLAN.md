# Image Search Backend — Refactor Plan

**Goal.** Make the backend easier to extend, easier to read, and harder to
break. No functional changes; the public API contract and test suite are
the regression net.

**Strategy.** One sweep on `dev`, not 8 stacked PRs. Isaac confirmed
2026-10-15: *"These shouldn't have been separate PRs in the first place. It
is a big sweep for refactoring. Just merge into dev and work in dev branch
for this."* So all work below lands directly on `dev` (or on a feature
branch that gets merged to `dev`). No more `--base main` PRs for refactor
work; the cumulative diff against `main` keeps growing and that was the
original problem. `dev` is the integration branch for this sweep.

**Out of scope (will NOT be touched in any work below):**
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

## What landed (2026-10-15)

| Phase | Title | Status |
|---|---|---|
| 1 | Suppression discipline + lint tightening | DONE (1dd859e) — 62 `type: ignore` markers removed, return types widened to `SearchResponse \| JSONResponse`, named justifications for remaining suppressions |
| 2 | Module layout (router / compute / deck / config split) | DONE (eaf2e2c) — extracted `RandomSessionStore` to `search/random_session.py`, moved `build_surprise_deck` to `search/surprise_deck.py`, deduped `_bad_request`/`_coerce_view` against `search/_result_helpers.py` canonicals, added `__all__` to public modules. Cross-router import gone. |
| 3a | Type-arg pass on `search/` | DONE (5a055a4) — fixed all 70 `type-arg` errors in `search/`. Parametrized `dict`/`list`/`tuple`/`Task`/`Popen`, added `SearchHit`/`SearchResult` imports. No behavior changes. |

**Merged into dev as a single merge commit** (63a112d, 2026-10-15) — 27 files, +557/-332.

## What still needs to be done (continues on dev)

| Phase | Title | Notes |
|---|---|---|
| 3b | Call-arg + no-untyped-def pass on `search/` | 47 call-arg + 30 no-untyped-def = 77 mypy errors. Mostly missing/wrong kwargs on Pydantic models and missing function signatures on helpers. |
| 3c | Remaining mypy pass (arg-type, dict-item, no-untyped-call, assignment, return-value, override, attr-defined) | ~50 errors scattered. Each is its own investigation; not as mechanical as 3a/3b. |
| 4 | Replace ad-hoc dicts with typed payload models | `SearchHit`, `CentroidSearch`, `SurpriseContext`. Also where the `app.py` nested `_parse_*` / `_rows_to_results` duplicates get hoisted to module level. The 2131-line god module. |
| 5 | Centralize env-var parsing into a pydantic-settings class | One `Settings` class instead of scattered `os.environ.get(...)` calls. |
| 6 | RFC 7807 error envelopes + global exception handlers | `application/problem+json` responses instead of `{detail: ...}`. |
| 7 | API surface review + naming consistency | Drop back-compat aliases, normalize route param names. |
| 8 | Final tightening: `Protocol`s, `__all__` everywhere, module docstrings, CI mypy gate on `search/` | Lock the new types in. |

**Total mypy errors remaining on `search/` after phase 3a:** 147 across 21 files.

**Hard rules (don't violate):**
- No behavior changes. Tests must keep passing (1517/1517 baseline). No "while I'm in there" bug fixes.
- Each commit is coherent; commits are not "atomic per file."
- Run `ruff check .` and `pytest tests/ --ignore=tests/test_local_sync_perf_helpers.py` after each meaningful chunk. CI runs on `push:dev`.
- Don't touch `scripts/*` unless the mypy error is in a file the dev/CI workflow imports. CI doesn't gate `scripts/`, so errors there are out of scope for now.
- Don't touch `indexer/`, `image_search_kernel/`, or `frontend/` in this sweep.

**Acceptance criteria for the sweep:**
- `mypy search/` error count trends down (currently 147; target < 20)
- `ruff check .` clean
- `pytest tests/ --ignore=tests/test_local_sync_perf_helpers.py` = 1517/1517 pass
- Public API contract unchanged (snapshot test in `tests/test_openapi_stability.py` still passes)
- `search/app.py` shrinks from 2131 lines to < 800
- `search/routers/centroids_search.py` shrinks from 600+ lines to < 300
- All public modules have `__all__` declared
- New types live in dedicated modules (not nested inside `app.py` closures)
