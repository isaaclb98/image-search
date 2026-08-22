# ADR-0005: Lazy cache refresh on the search-side index

**Status:** Proposed — not yet implemented.
**Date:** 2026-08.
**Related:** `docs/backend-refactor-plan.md` §4.7, §4.8, §B5.

## Context (problem statement)

`search/index_db.py` is the synchronous SQLite cache of Qdrant photo metadata. Its warm-up cost is a full Qdrant scroll + per-row SQLite insert. Today the warm-up runs at app startup inside the lifespan handler, so the cold-start budget includes the scroll cost. On a 60K-point collection this is a non-trivial delay before the first request can be served.

A second consequence: when Qdrant changes (a write from the indexer adds a point) the cache is stale until the next refresh cycle. Today the refresh is bounded by a TTL (`refresh_interval_seconds`); readers either serve stale data or trigger an expensive re-scroll.

## Decision (proposed)

Two changes:

1. **Async driver.** Replace synchronous `sqlite3` with `aiosqlite`. The `IndexDB` class becomes `async def`; route handlers `await` the methods. Removes the `asyncio.to_thread` wrappers around every method call and frees the request thread-pool.

2. **Lazy / background refresh.** App startup completes without hydrating the cache from Qdrant. The first read after startup triggers a hydrate. A background task continues to refresh on the configured TTL while the app serves requests from whatever cache state exists.

The refresh itself uses the same Qdrant scroll it uses today; only the lifecycle changes.

The two changes are coupled: a lazy refresh only buys you startup latency if the reader is async (otherwise the first read still blocks the event loop).

## Consequences

- **Positive:** Cold-start drops. The model's expensive load (separate, §C4) becomes the dominant cost; cache hydration is invisible to the operator.
- **Positive:** A background refresh keeps the cache warm without holding up requests.
- **Positive:** The TTL-based refresh loop becomes a single async task rather than a thread that blocks on SQLite.
- **Negative:** Adds an `aiosqlite` dependency.
- **Negative:** The `IndexDB` class becomes async; every call site (route handlers, `for_you`, `discover`, `centroids`, the cache refresh task itself) must be updated. The conversion is mechanical.
- **Negative:** `aiosqlite`'s connection is single-threaded; concurrent writes serialize. The write rate is sub-Hz today; this isn't a real constraint, but it's a property of the change.

## Why not

- **Cache on every read.** Rejected: a 60K-row cache re-read on every request is far more expensive than a periodic refresh, even with the thread-pool cost. The cache is a hot read path; re-reading it on every request is the wrong direction.
- **Drop SQLite, use an in-memory cache.** Rejected: the cache holds per-photo user state (favorites, dislikes) that must survive restarts. An in-memory cache loses state.
- **Use Redis.** Rejected: adds an external service dependency for a single-host deployment.

## Verification (planned, post-implementation)

- Cold-start benchmark (§4.9) drops below 5s (post-model-load).
- First-request latency within budget (no worse than current cache-warm path).
- Background-refresh task does not block requests; verified by a test that fires concurrent requests during a refresh and asserts no request waits for the refresh.
- Existing `tests/test_index_db.py` keeps passing after the methods are made `async`.

## Status

This ADR is **Proposed**. Implementation lands in Phase B5 of the plan. Tracking issue: track in the repo's issue tracker or a TODO comment in `search/index_db.py`.
