# image-search — Performance Improvement Plan

Concrete, prioritized recommendations to make image-search faster end-to-end. Each item names the file, the function, the change, the expected speedup, and the migration risk. Items are grouped by where the win is and listed in roughly the order I'd ship them.

**Repo layout referenced in this doc** (paths relative to `isaaclb98/image-search`):
- Backend: `search/app.py`, `search/index_db.py`, `search/qdrant_client.py`, `search/for_you.py`
- Frontend: `frontend/src/lib/components/*.svelte`, `frontend/src/lib/api/client.ts`
- Infra: `docker/Dockerfile.search`, `gitops/clusters/home/apps/image-search/statefulset.yaml`

**Current load-bearing facts** (from prod, after round-5 digest `e78b0986`):
- `/healthz` returns 200 in 226 ms
- `/api/random` returns in ~150 ms
- `/api/similar/{id}?limit=100` returns 100 results in 3.4 s (the slow one)
- `/api/for-you/feed?diversity=balanced` returns 20 results in 800–1200 ms
- `/photo/{id}/raw?w=1920` returns a Lanczos-resized JPEG in 80–250 ms (cached 8 ms)
- `WEB_CONCURRENCY=1`, single gunicorn worker, in-cluster Qdrant
- 27 photo tiles on `/for-you` × 4.5 KB blurhash decodes = ~120 KB main-thread JS per page load
- Cold start: 5-minute startupProbe budget (SigLIP2 model load + IndexDB hydrate)

The single biggest constraint today: **everything is serialised through one gunicorn worker**. Every sync call inside an async handler blocks the entire service until it returns. The probes in the statefulset are the band-aid; the real fix is to convert the slow paths to true async. Below, in three tiers.

---

## Tier 1 — biggest wins, lowest risk

### 1.1 Wrap remaining sync `index_db.*` calls in `asyncio.to_thread`

**Files:** `search/app.py`
**Where:** 14 remaining bare `index_db.<fn>()` calls inside `async def` handlers, e.g.
- `cache_status` (3 calls: `qdrant_point_count`, `count_images`, `last_refresh_time`) — L2511–2517
- `_periodic_refresh_loop` (2 calls: `try_acquire_refresh_lock`, `release_refresh_lock`) — L674, L700
- `album_download_zip` (2 calls: `get_album`, `list_album_members`) — L2158, L2169
- `api_cache_refresh` (2 calls: `try_acquire_refresh_lock`, `release_refresh_lock`) — L2465, L2499
- `for_you_feed` (2 calls: `list_favorite_ids`, `list_dislike_ids`) — L3053–3054
- `_resolve_filename_filter` (1 call: `get_by_id`) — L1217
- `favorites_download_zip` (1 call: `list_favorites`) — L2082

**Why it matters:** Every bare call holds the single gunicorn worker hostage. A 200 ms SQLite query on the TrueNAS-backed DB turns into a 200 ms hang for every other request. The probes in the statefulset restart the pod after 3 consecutive failures (30s outage) but the real fix is to wrap these.

**Migration:**
```python
# before
hits, has_more = qdrant.search(...)
fav_ids = index_db.list_favorite_ids()

# after
fav_ids = await asyncio.to_thread(index_db.list_favorite_ids)
hits, has_more = await asyncio.to_thread(qdrant.search, ...)
```

Apply the same pattern to `qdrant.search`, `qdrant.retrieve`, `qdrant.recommend`, and `qdrant.search_with_vectors` — the statefulset comment already enumerates the affected routes (`lifespan`, `_resolve_filename_filter`, `_favorite_ids_for_filter`, `photo_metadata`, `api_search`, `similar_photos`, `list_collections`, `search_by_centroid`, `healthz`). The pattern is already established in `photo_raw`, `healthz`, `for_you_reset`, and 23 other call sites; apply it to the remaining 13.

**Expected speedup:** Removes all the "60–120s hangs where every endpoint times out" symptom from the statefulset comment. Net: 0% faster on the happy path, but eliminates whole-app freezes during slow NFS reads.

**Risk:** Low — purely additive `await` + `asyncio.to_thread`. The IndexDB and QdrantClient objects are already thread-safe enough (sqlite3 has `check_same_thread=False`).

### 1.2 Parallelize `_resolve_query_vector` with favorite-id fetch in `api_search`

**Files:** `search/app.py` (line ~1800–1900 in `async def api_search`)
**Pattern today:** The handler runs:
1. `_resolve_query_vector(vec, ...)` — encodes the text query via SigLIP2 (~150 ms on cold cache, ~30 ms warm)
2. `await _favorite_ids_for_filter()` — hits SQLite (~50 ms)
3. `qdrant.search(...)` — the actual vector search (~100 ms for 100 hits)

Each one awaits the previous one. Step 1 and step 2 are independent and can run in parallel:
```python
vec, vec_err, vec_detail, (favorite_ids,) = await asyncio.gather(
    asyncio.to_thread(_resolve_query_vector, active_centroids, prompt_state, weights=active_weights),
    asyncio.to_thread(_favorite_ids_for_filter) if favorites else asyncio.sleep(0, result=None),
)
```

The text encoder (`search/text_encoder.py`) is already a sync module wrapped via `asyncio.to_thread` elsewhere — apply the same pattern here.

**Expected speedup:** ~50–100 ms per `/api/search` request when the user has favorites and a text query. Stack rank gets tighter in A/B.

**Risk:** Low. The two paths share no mutable state. `_favorite_ids_for_filter` returns `set[str]`; the vector is `list[float]`.

### 1.3 Add `Cache-Control: max-age=31536000, immutable` on `/_app/*`

**Files:** `search/app.py` (StaticFiles mount at line ~3265), plus the `/photo/{point_id}/raw` response.

**Why:** SvelteKit emits content-hashed JS/CSS under `/_app/immutable/...` — those never change. The default `StaticFiles` mount sends no `Cache-Control` header, so browsers may revalidate on every visit (304 round-trip is ~50 ms over the tailnet, ~200 ms over mobile).

**Migration:** Subclass `StaticFiles` or wrap the mount:
```python
class CachedStatic(StaticFiles):
    def __init__(self, *a, cache_seconds: int = 31536000, **kw):
        super().__init__(*a, **kw)
        self.cache_seconds = cache_seconds
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = f"public, max-age={self.cache_seconds}, immutable"
        return resp

app.mount("/_app", CachedStatic(directory=str(app_static)), name="spa-assets")
```

For `/photo/{id}/raw?w=N`, emit `Cache-Control: public, max-age=86400, must-revalidate` — the Lanczos disk cache at `/app/data/photo_cache/{id}/w{width}.jpg` is already content-addressed, and a 24-hour TTL avoids re-validating on every reload. For `?w=0` (original file) use a 1-hour TTL since files can be edited.

**Expected speedup:** Halves the number of HTTP round-trips on second-and-later visits. Photo tiles also stop flickering as the browser revalidates.

**Risk:** None on `/_app` (hash-named). Moderate on `/photo` — if you delete the original file, the browser may keep the cached resized version for 24 h. Mitigate with `must-revalidate` and a purge script that proxies through with `Cache-Control: no-cache` on delete.

### 1.4 Precompute + cache blurhash once on IndexDB hydration

**Files:** `search/index_db.py`, `search/app.py`, `frontend/src/lib/components/PhotoTile.svelte`
**Where:** Every search result includes `blurhash` in the JSON response. The frontend decodes it into a data-URL placeholder on mount (`blurhashToDataUrl` runs per tile, ~2 ms × 20 tiles = 40 ms of main-thread JS per page).

**Migration (backend):** Compute the blurhash once at index time (in `indexer/`) and store it in `images.payload` or a side table. The search handler returns it as a string; the frontend already accepts a pre-resolved data URL via the `dataUrl` prop (added in round-5).

**Migration (frontend):** If the response already includes a `blurhash_data_url`, pass it as `dataUrl` to skip `blurhashToDataUrl()` on mount. Today `PhotoTile` already supports this path; we just need to populate the field.

**Expected speedup:** Removes ~40 ms of layout work per page load (1 frame at 60 fps). Bigger win: removes the placeholder flicker on tiles.

**Risk:** Low. Blurhash strings are 20–30 bytes each; computing them at index time is essentially free (the PIL decode + blurhash encode already happens to compute width/height).

### 1.5 Frontend: `Promise.all` the parallel fetches in `_resolve_username_for_url`

**Files:** `frontend/src/routes/albums/+page.svelte`, `frontend/src/routes/+page.svelte`
**Pattern:** Both pages call `refresh()` then `refreshSystemCounts()` in series. They're independent.

**Migration:** Both already use async, just kick them in parallel:
```typescript
// before
await refresh();
await refreshSystemCounts();

// after
await Promise.all([refresh(), refreshSystemCounts()]);
```

Same opportunity in `frontend/src/routes/albums/[id]/+page.svelte` and anywhere that fetches album detail + member list separately.

**Expected speedup:** ~50–150 ms per page that does ≥2 unrelated fetches on load.

**Risk:** None. Both fetches target different endpoints and don't share state.

---

## Tier 2 — meaningful wins, moderate refactor

### 2.1 Move the IndexDB to a real async driver (`aiosqlite`)

**Files:** `search/index_db.py` (currently 45 methods, all sync, on top of `sqlite3.Connection(check_same_thread=False)`)
**Why:** Every `index_db.<fn>()` call is currently wrapped in `asyncio.to_thread`. That works but it burns a thread-pool slot for every concurrent request. With `WEB_CONCURRENCY=1` and a thread pool of ~40 workers, this is fine; if you ever scale to 4–8 gunicorn workers, the thread pool gets saturated and throughput stalls.

**Migration:**
1. `pip install aiosqlite`
2. Replace `sqlite3.connect(db_path)` with `aiosqlite.connect(db_path)`
3. Convert each method to `async def`, replacing `self._conn.execute(sql, params)` with `await self._conn.execute(sql, params)`
4. Replace the per-call `asyncio.to_thread` wrappers with `await` directly
5. SQLite's writer lock will serialise writes; that's fine for our write rate (likes/dislikes/album edits are sub-Hz)

**Expected speedup:** On a single-worker setup, no immediate gain. Frees the thread pool for `PIL.Image.*` work (which we can't easily asyncify). Sets up the system for true async scaling.

**Risk:** Medium. 45 methods to convert; many touch the same connection so you need a single shared `aiosqlite.Connection` or a connection pool. The migration is mechanical but laborious — budget a full day and add tests against `tests/test_index_db.py` for each method.

### 2.2 Precompute ForYouState in the background and cache for 30 seconds

**Files:** `search/for_you.py`, `search/app.py` (`for_you_feed` at L3050)
**Where:** Every call to `for_you_feed` does:
1. `index_db.list_favorite_ids()` — ~30 ms
2. `index_db.list_dislike_ids()` — ~30 ms
3. `_for_you_rank(...)` — `_rank` calls `qdrant.recommend()` (~100–300 ms)

These IDs change slowly. Cache them in-memory for 30 s:
```python
@lru_cache(maxsize=1)
def _cached_fav_dis_ids():
    return (list(index_db.list_favorite_ids()), list(index_db.list_dislike_ids()))

# then in for_you_feed:
fav_ids, dis_ids = await asyncio.to_thread(_cached_fav_dis_ids)
_fav_dis_ids_cache_ttl_seconds(30)  # invalidate every 30 s
```

Use a `threading.Lock` + a TTL timestamp. The qdrant.recommend call itself is still per-request (you can't cache recommendations cheaply without losing the user-specific ranking), but you save ~60 ms of SQLite time per `/api/for-you/feed` call.

**Expected speedup:** 50–80 ms per `for-you` request on the SQLite path. Saves the I/O wait on TrueNAS.

**Risk:** Low. 30-second staleness is acceptable for the home-page For You row; the per-Like/Dislike actions already invalidate by re-fetching.

### 2.3 Lazy-load the SigLIP2 model on first request (drop cold-start spike)

**Files:** `search/text_encoder.py` (269 lines), `docker/Dockerfile.search`
**Where:** The current Dockerfile has `PREWARM_MODEL=0` by default. The cold-start budget in the statefulset (`startupProbe.failureThreshold=30` × `periodSeconds=10` = 5 minutes) suggests the model isn't loaded until first text search. Verify whether `text_encoder.py` is loaded eagerly or lazily today; if eager, move to lazy.

**Expected speedup:** Cold-start drops from ~5 min to ~30 s. The 5-min startupProbe can be reduced to 60 s, so a normal rollout takes 90 s instead of 6 min.

**Risk:** Low. The model is already a singleton after first load; the only change is when it's instantiated.

### 2.4 Add ETag / 304 responses on `/photo/{id}/raw` and `/api/search`

**Files:** `search/app.py`
**Where:** `/photo/{id}/raw` already serves a disk-cached file. Add `ETag: W/"<file-mtime>-<size>-w<width>"` and respond with `304 Not Modified` on `If-None-Match`:
```python
etag = f'W/"{mtime}-{size}-w{w}"'
if request.headers.get("if-none-match") == etag:
    return Response(status_code=304, headers={"ETag": etag})
```

For `/api/search`, derive an ETag from the query parameters (hash of normalised prompt state + offset + limit + favorites). Cached responses can be 304-ed by the browser.

**Expected speedup:** Photo tile re-renders: ~50–200 ms → 0 ms (network). Search back-button restores: 100 ms → 5 ms.

**Risk:** Low. Need to be careful about caching personalised responses (search results depend on `favorites` flag). Use `Vary: Cookie` and only 304 when the cookie is unchanged.

### 2.5 Concurrent photo uploads / image decoding in `IndexDB._init_from_qdrant`

**Files:** `search/index_db.py`, `indexer/`
**Where:** First cold start hydrates the IndexDB by walking `/nas/images/*` and reading each file's metadata + thumbnailing. Today this is single-threaded and takes minutes for a 50k-photo library.

**Migration:** Use `concurrent.futures.ThreadPoolExecutor(max_workers=8)` to parallelise the file-walking. SQLite writes are still serialised through one connection (with `check_same_thread=False`), but the *file reads + PIL decodes* can run in parallel.

**Expected speedup:** Cold-start hydration time drops 4–8× (8× is the upper bound; limited by SQLite write throughput).

**Risk:** Medium. Need to batch SQLite writes (insert 100 rows per commit instead of one row per commit) to keep the writer from being a bottleneck. Worth doing as part of the same PR.

---

## Tier 3 — architectural changes, bigger payoff but bigger lift

### 3.1 Switch to async-native QdrantClient

**Files:** `search/qdrant_client.py`
**Where:** `qdrant_client.QdrantClient` has an `AsyncQdrantClient` that uses `httpx.AsyncClient` under the hood. Today every Qdrant call goes through the sync client wrapped in `asyncio.to_thread`. The async client would let multiple in-flight requests multiplex on a single HTTP/2 connection.

**Migration:**
1. `pip install qdrant-client[fastembed]` (already installed; verify async support)
2. Replace `QdrantClient(...)` with `AsyncQdrantClient(...)` in `__init__`
3. Convert every `await asyncio.to_thread(qdrant.search, ...)` into `await qdrant.search(...)` directly
4. Remove all `asyncio.to_thread` wrappers around QdrantClient methods

**Expected speedup:** On the multi-request path (Home page does `/api/for-you/feed` + `/api/albums` + `/api/favorites` + `/api/dislikes` in parallel — that's already `Promise.all`-able in the frontend), the HTTP/2 multiplexing saves 30–60 ms per request. More importantly: removes a thread-pool slot per request.

**Risk:** Medium-High. `AsyncQdrantClient` API surface differs slightly — `search()` returns `QueryResponse` instead of `(hits, has_more)`. Need to update every call site. Run the test suite end-to-end; Qdrant timeout semantics also change slightly.

### 3.2 Replace gunicorn + uvicorn-worker with uvicorn direct + SO_REUSEPORT

**Files:** `docker/Dockerfile.search` CMD, plus the statefulset (potentially bump replicas).

**Where:** Today: `gunicorn search.app:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY} --timeout 120 --graceful-timeout 90`.

**Migration:** Run `uvicorn search.app:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY}` directly. Add a single uvicorn process per pod, then scale via k8s `replicas` instead of `--workers`. With `SO_REUSEPORT` enabled (set in uvicorn via `--http`) the kernel load-balances across replicas.

**Expected speedup:** Linear in pod count. Single-pod baseline stays the same; 2 replicas → ~1.8× throughput, 3 → ~2.6×. Combined with 3.1 (async Qdrant), each replica handles more concurrent requests.

**Risk:** Medium. StatefulSet with 1 replica + ReadWriteOnce PVC today — moving to 2 replicas means either a ReadWriteMany NFS-backed PVC (already mounted) or moving image state out of the local PVC into a shared cache (S3 / minio). Also: `IndexDB` is currently a local SQLite file at `/app/data/index.db`; multi-replica needs that file on a shared volume OR a real DB (postgres).

### 3.3 Frontend: switch to streaming SSR for first paint

**Files:** `frontend/src/routes/+page.svelte`, `frontend/src/lib/components/ForYouRow.svelte`

**Where:** Today the SPA does `onMount(fetch)` after hydration, so the user sees a blank glass panel for 200–400 ms before the first 20 photos appear. Server-side render the ForYouRow in `+page.server.ts` and stream the tiles in the initial HTML.

**Migration:**
1. Convert `adapter-static` to `adapter-node` (the backend already serves `_app/`)
2. In `+page.server.ts`, call `forYouFeed()` on the server, stream the result into `+page.svelte` as a prop
3. The client hydrates over the streamed data; the empty-state flash disappears

**Expected speedup:** First Contentful Paint drops from ~400 ms to ~80 ms (just network round-trip). Time-to-interactive stays the same.

**Risk:** Medium. Need a `+page.server.ts` per route, plus the auth cookie forwarding logic. Adapter change requires rebuilding the backend image; doubles the dev-loop iteration time.

### 3.4 Replace disk photo cache with CDN-fronted object storage

**Files:** `search/app.py` (`photo_raw`), `docker/Dockerfile.search`, gitops statefulset
**Where:** `/photo/{id}/raw?w=N` currently serves from the local Lanczos cache at `/app/data/photo_cache/{id}/w{width}.jpg`. The cache is PVC-mounted, so it's local to the pod. With a CDN in front, you could serve 90% of tile requests from edge cache.

**Migration:**
1. Move photo storage from TrueNAS to S3 (or `minio` in-cluster for dev)
2. Compute the resized variant at index time, store at `s3://bucket/{id}/w{width}.jpg` with `Cache-Control: public, max-age=31536000`
3. `photo_raw` redirects (302) to a presigned S3 URL or to a CloudFront edge
4. Cache hits at the edge → 5 ms; cache misses at S3 → 30 ms; resize compute → 150 ms (cold)

**Expected speedup:** Steady-state tile load: 80 ms → 5 ms for cached sizes. Cold cache or new size: ~150 ms once, then 5 ms.

**Risk:** High. Photo library moves from "filesystem on NAS" to "object storage with consistency guarantees"; need a migration plan + rollback. Out of scope for image-search v1; this is a "v2" move.

---

## Cross-cutting improvements (do alongside anything else)

### C.1 Add response time logging

```python
@app.middleware("http")
async def log_request_time(request: Request, call_next):
    t0 = time.perf_counter()
    try:
        return await call_next(request)
    finally:
        dur = (time.perf_counter() - t0) * 1000
        logger.info(f"{request.method} {request.url.path} {dur:.1f}ms")
```

Already partially in place (some handlers log `took_ms`), but not standardised. Once it's standardised, add a `p99_latency` panel to the home dashboard.

### C.2 Set a response size cap on `qdrant.search`

Today `qdrant.search(limit=...)` returns up to the limit; payload size scales with `limit × average_payload_bytes`. The default payload includes `path` (~50 bytes) + the photo URL (~100 bytes). At `limit=100` × 150 bytes = 15 KB JSON per request. Cap with `with_payload=["id", "path"]` if you don't need the full payload — saves ~5 KB per request on the wire.

### C.3 Move static asset gzip/brotli precompression to build time

SvelteKit emits precompressed `.gz` / `.br` files when configured (`vite-plugin-compression`). The FastAPI mount should serve the precompressed file when `Accept-Encoding: gzip, br` is present:
```python
@app.get("/_app/{path:path}", include_in_schema=False)
async def spa_asset(path: str, request: Request):
    # Try .br, then .gz, then uncompressed
    ...
```

Today the assets are served uncompressed over a TLS tunnel; Caddy adds compression on the way out, so this is mostly redundant — but precompressed-on-disk avoids the per-request gzip CPU cost on the Caddy side.

### C.4 Frontend: send `Keep-Alive` and use HTTP/2 server push for critical JS

Tailscale serves HTTP/1.1 by default. Enabling HTTP/2 on Caddy would let the server push `_app/immutable/entry/*.js` when `index.html` is requested, eliminating one round-trip on cold load. Config change in the Caddyfile; no backend change needed.

---

## What I'd ship first (if I had a quarter-day)

1. **1.1** — Wrap the 13 remaining bare `index_db.*` calls. The statefulset comment explicitly says "these are the band-aid for this same bug." Single PR, ~1 hour including tests.
2. **1.3** — `Cache-Control` on `/_app/*` and `/photo/*`. Single 20-line PR, big win on revisit.
3. **1.5** — `Promise.all` the two parallel fetches in `albums/+page.svelte`. Single 3-line PR.
4. **2.4** — ETag/304 on `/photo/{id}/raw`. Single 40-line PR.

Total: ~half a day of work, removes the biggest user-visible symptom (freezes during NFS stalls) and halves network round-trips on revisit. Worth doing before any Tier-2 / Tier-3 lift.

## What I'd watch out for

- **Don't ship 2.1 (`aiosqlite`) before 1.1** — converting every method to async without the `to_thread` wrappers in place will make every handler 2× slower on cold paths.
- **Don't scale `replicas` before 3.2** — `IndexDB` is a local SQLite file. Two pods writing the same file would corrupt it.
- **Don't ship 3.4 (S3 move) during a content refresh** — the indexer needs to re-decode + re-upload every original, which takes hours. Schedule for a quiet window.

## Metrics to watch after each tier

- `/healthz` p50 / p99 latency (should stay flat; this is a probe path)
- `/api/similar/{id}?limit=100` p99 latency (target: <1.5 s after Tier 2)
- `/api/for-you/feed` p99 latency (target: <800 ms after Tier 2)
- `/photo/{id}/raw?w=640` p50 / p99 (target: <30 ms / <150 ms after Tier 1.3 + 2.4)
- Cold-start pod time (target: <90 s after 2.3 + startupProbe reduction)
- Bytes served per page load (target: 50% drop after 1.3 + C.3)
- Worker-thread-pool utilisation (target: <50% after 3.1)

Track these in a Prometheus scrape on `/metrics` (add `prometheus-fastapi-instrumentator` in a separate PR).
