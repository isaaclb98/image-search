# ADR-0006: Async-native Qdrant client on the search side

**Status:** Proposed — not yet implemented.
**Date:** 2026-08.
**Related:** `docs/archive/backend-refactor-plan.md` (archived) §4.8, §C1.

## Context (problem statement)

`search/qdrant_client.py` wraps `qdrant_client.QdrantClient`, the synchronous client. Every Qdrant call from a request handler is wrapped in `asyncio.to_thread` to avoid blocking the event loop. The wrapping works but:

- It burns a thread-pool slot per concurrent request. The default thread pool is ~40; under load the pool saturates.
- It uses HTTP/1.1, not HTTP/2. Multiplexing multiple in-flight requests on a single connection doesn't happen.
- It hides the type signature: every `await asyncio.to_thread(qdrant.search, ...)` looks the same regardless of the actual qdrant-client API shape.

## Decision (proposed)

Replace `qdrant_client.QdrantClient` with `qdrant_client.AsyncQdrantClient` everywhere on the search side. The wrapper at `search/qdrant_client.py` exposes the same `QdrantSearch` interface but is built on the async client.

Every call site that today does:

```python
hits = await asyncio.to_thread(qdrant.search, ...)
```

becomes:

```python
hits = await qdrant.search(...)
```

The `to_thread` wrapper disappears. The handler awaits directly. The async client multiplexes in-flight requests on a single HTTP/2 connection (per `qdrant-client` docs).

The indexer side is unchanged. Indexer pipeline runs are sync (§4.4.1); they make blocking calls in their own threads. Switching the indexer to async is a separate decision and not part of this refactor.

Connection-pool sizing follows §4.8 of the plan: `min(uvicorn_workers * 4, 32)` per pod.

## Consequences

- **Positive:** Removes a thread-pool slot per concurrent request. Multi-worker pods (or future scale-out) stop being limited by the pool size.
- **Positive:** HTTP/2 multiplexing saves 30–60 ms per request on the multi-fetch paths (`/api/for-you/feed` does multiple Qdrant calls in series today).
- **Positive:** Cleaner type signatures; no `asyncio.to_thread` boilerplate in handlers.
- **Negative:** Migration is mechanical but touches every Qdrant call site. `AsyncQdrantClient`'s API surface is similar to the sync one but `search()` returns a `QueryResponse` object (not `(hits, has_more)`); the wrapper at `search/qdrant_client.py` adapts the shape so call sites don't need to change.
- **Negative:** Some timeout semantics differ between the sync and async clients. Tests must verify timeouts still behave as expected.

## Why not

- **httpx directly, no qdrant-client.** Rejected: `qdrant-client` is the canonical wrapper; bypassing it loses the gRPC / HTTP/2 protocol handling and the schema-aware Point/Filter types.
- **A separate sidecar Qdrant client module.** Rejected: the wrapper already exists at `search/qdrant_client.py`; replacing the inner client is a one-file change.

## Verification (planned, post-implementation)

- `tests/test_search_api.py` keeps passing (existing tests cover the wrapper's API shape).
- A new integration test verifies that two slow requests fired in parallel do not block each other.
- Query-latency benchmark (§4.9) shows the p95 reduction.
- The full test suite has no regressions from the wrapper change.

## Status

This ADR is **Proposed**. Implementation lands in Phase C1 of the plan.
