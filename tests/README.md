# tests/ — organization and naming convention

This directory has ~95 test files. They're organised along two
axes — **what is being tested** and **how the test is wired**.
The two axes show up in the filename:

## What is being tested

Most files are named after the module under test:

| Filename pattern        | Module being tested                  |
|-------------------------|--------------------------------------|
| `test_models.py`        | `search/models.py`                   |
| `test_config.py`        | `search/config.py`                   |
| `test_index_db.py`      | `search/index_db.py`                 |
| `test_qdrant_client.py` | `search/qdrant_client.py`            |
| `test_diversity.py`     | `search/diversity.py`                |
| `test_*.py`             | the matching `search/*.py` module    |

## How the test is wired

A second suffix tells you the wiring:

| Filename pattern               | Wiring                                            |
|--------------------------------|---------------------------------------------------|
| `test_X_unit.py`               | Pure unit test. No DB, no Qdrant, no app fixture.  |
|                                | Mocks only. Runs in <50ms. Always pick this when |
|                                | the test doesn't need state.                     |
| `test_X.py`                    | Integration test using `app_with_qdrant` or       |
|                                | similar fixture. Hits a real (in-memory) Qdrant. |
|                                | The default choice for behavioural tests.         |
| `test_X_api.py`                | API-level test using the public HTTP shape.       |
|                                | Treats the FastAPI app as a black box. Used for  |
|                                | tests that exercise the full request/response    |
|                                | cycle (status codes, headers, error envelopes).   |
| `test_routers_X.py`            | Router contract test. Mocks the dependencies      |
|                                | (`qdrant`, `index_db`, `cfg`) and pins the        |
|                                | router's input/output contract in isolation.     |
|                                | Pairs with `test_X.py` which exercises the        |
|                                | same endpoints through the full app.             |
| `test_X_zip.py`                | Tests a download-zip endpoint. Pulled out so the  |
|                                | zip code path doesn't bloat the main test file.  |
| `test_X_*.py` (other suffixes) | Test a specific sub-feature of the module.        |
|                                | E.g. `test_local_sync_dims.py` for the per-point  |
|                                | dimension pinning in the local-sync subsystem.   |
| `test_architecture.py`         | Cross-cutting / structural tests. (Imports       |
|                                | blacklisted modules, asserts on shape, etc.)     |
| `test_v2_smoke.py`             | "Make sure it actually starts" smoke test. Run    |
|                                | last in CI.                                       |

## When to use which

Default to `test_X.py` for new behavioural tests. Reach for
`test_X_unit.py` only when:
- the test runs in <10ms and you'd otherwise wait for it
- the test has zero IO and you want to assert a pure-function
  contract
- you're pinning a helper that has many small edge cases
  (e.g. `_result_helpers.parse_weights`)

`test_X_api.py` is for tests that need to assert the JSON
envelope shape against the public API surface. If your test
could equally be written as a router-level test with mocks,
write it that way instead — it's faster and the failure
message points at the actual broken layer.

`test_routers_X.py` is for tests that need to assert a single
router's contract without the rest of the app. The
`build_X_router(...)` factory takes mocked dependencies so you
can pin the contract without spinning up Qdrant. Pairs with
`test_X.py` which exercises the same endpoints through the
full app — the two together give you both fast failure
isolation and end-to-end coverage.

## Anti-patterns to avoid

- `test_X_integration.py` and `test_X_int.py` — pick one
  convention. We use `test_X.py` (no suffix) for integration
  because that's the dominant pattern and adding a suffix
  doesn't add information.
- `test_X_yyyymmdd.py` — no date-stamped test files. If a
  test is a one-off validation, fold it into the matching
  `test_X.py` or delete it after the validation passes.
- `test_X_smoke.py` — only the top-level smoke test gets
  that suffix. If you need a smoke test for a specific
  module, name it `test_X.py` and tag it with a marker.

## File naming consistency audit (Oct 2026)

Known mixed pairs that the team has decided are intentionally
separate (don't merge without a discussion thread):

- `test_indexed_helpers.py` (integration) +
  `test_indexed_helpers_unit.py` (unit) — split kept so the
  unit suite stays fast.
- `test_lazy_index_cache.py` (integration) +
  `test_lazy_index_cache_unit.py` (unit) — same reason.
- `test_dislikes.py` (full-app integration) +
  `test_routers_dislikes.py` (router contract) — see the
  docstring of `test_routers_dislikes.py` for the rationale.
- `test_favorites_api.py` (public API) +
  `test_routers_favorites.py` (router contract) — same.
