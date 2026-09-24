# Architecture

Module-by-module reference for the three runnable halves (`search/`,
`indexer/`, `image_search_kernel/`) and the SvelteKit frontend.

For the wire-format side of the API, see the
generated OpenAPI at `/openapi.json` (dev: <http://localhost:8000/openapi.json>).

---

## Top-level shape

- **One image, one port.** `docker/Dockerfile.search` builds the
  SvelteKit SPA with `adapter-static`, bakes it in, and FastAPI serves
  both `/api/*` and the SPA from `:8000`. No nginx, no separate SPA
  container. See `docker/docker-compose.yml` for the production topology.
- **Qdrant sidecar.** Vector store runs in its own container;
  `QDRANT_URL` env var tells the backend where to reach it.
- **No auth.** The frontend never sees a login screen, the backend
  exposes no auth router, there's no `search/auth.py`. Deploy behind a
  reverse proxy (caddy, oauth2-proxy, tailscale serve) if access
  control is needed. See `README.md` ("Auth: None").

---

## `search/` — FastAPI app

The search side is a pure JSON API. It does not serve HTML, it does not
host the SPA build. The SvelteKit frontend talks to it over HTTP/JSON.

### App wiring

- **`search/app.py`** — `create_app()` factory. Wires the Qdrant
  client, IndexDB, middleware (CORS, request logging), the static mount
  for SPA fallback, and every router from `search/routers/`. Owns the
  `/api/photo/{point_id}` JSON endpoint, the `/photo/{id}/raw` image
  streamer (with cache headers and path-liveness checks), and the two
  streaming ZIPs (`/favorites/download.zip`, `/albums/{id}/download.zip`).
- **`search/config.py`** — env-var parsing and `AppConfig` dataclass.
  Reads from process env (which `.env` populates via `python-dotenv`).
- **`search/middleware.py`** — request logging + CORS.
- **`search/qdrant_client.py`** — async wrapper around the Qdrant
  client.
- **`search/text_encoder.py`** — SigLIP2 text encoder wrapper. Mirrors
  the indexer's vision encoder so queries and points live in the same
  space. Returns unit-norm vectors.

### Dual store: Qdrant + SQLite

- **Qdrant** is the source of truth for vectors + payload. All search
  reads go through it.
- **`search/index_db.py`** — SQLite store. Holds two kinds of state:
  - **Rebuildable** cache of photo metadata that exists in Qdrant but
    is hot in the request path (folder, blurhash, mtime, etc.).
  - **Non-rebuildable** per-photo user state: favorites, dislikes,
    saved searches, album membership.

  Background refresh: `INDEX_DB_REFRESH_INTERVAL_SECONDS` (default 6h),
  or `POST /api/cache/refresh` to force it now.

- **`search/lazy_index_cache.py`** — B5 contract wrapper around
  `IndexDB`. Startup completes *without* hydrating from Qdrant; the
  first read triggers hydration, and the app serves from a stale
  (possibly empty) cache while a background task refreshes.

- **`search/image_resolver.py`** — turns a stored absolute path into
  the public `/photo/{id}/raw` URL the frontend embeds. Honors the
  optional `PATH_PREFIX` → `NAS_IMAGES_BASE` rewrite for cross-machine
  setups (index on Windows, serve on Linux).

### Ranking & feature compute

The big read-side features follow a `compute.py` ↔ service-module split:

- **`search/diversity.py`** + **`search/diversity_compute.py`** — applies
  byte-exact and perceptual-hash deduplication plus a relevance-drop
  MMR pass to ordinary search results. Tunable via `DIVERSITY_*` env vars.
- **`search/centroids.py`** + **`search/centroids_compute.py`** —
  centroid-driven queries (search by an album centroid instead of a
  text prompt).
- **`search/for_you.py`** + **`search/for_you_compute.py`** — the
  personalized feed that combines likes/dislikes with MMR over the
  library.
- **`search/random.py`** — uniform random sampling with optional
  folder bias.

The `compute.py` modules are pure functions (vectors + masks in, vectors
+ masks out, no I/O, no globals, no logging beyond debug invariants).
The service module is a thin orchestrator on top. Unit-tested in
isolation.

### Routing

One file per resource group in `search/routers/`. Each exports a
`build_<name>_router(...)` factory that returns an `APIRouter`; the
factory pattern lets tests inject mock dependencies.

| File | Routes | Notes |
|---|---|---|
| `search.py` | `GET /api/search` | Text + image + filename filter. The hot path. |
| `similar.py` | `GET /api/similar/{point_id}` | "More like this" by Qdrant Recommend. |
| `random.py` | `GET /api/random` | Uniformly random, optional folder bias. |
| `for_you.py` | `GET /api/for-you/feed`, `POST /api/for-you/reset` | Personal feed. |
| `favorites.py` | `POST/GET/DELETE /api/favorites[...]` | Per-photo favorite toggle + list + ZIP. |
| `dislikes.py` | `POST/GET/DELETE /api/dislikes[...]` | Per-photo dislike + list. |
| `albums.py` | `POST/GET/PATCH/DELETE /api/albums[...]` | Named user-curated sets; ZIP download. |
| `saved_searches.py` | `POST/GET/DELETE /api/saved-searches[...]` | Named search recipes. |
| `centroids_list.py` | `GET /api/centroids` | List loaded centroids. |
| `centroids_search.py` | `GET /api/centroids/{name}/search` | Search with a centroid as the query vector. |
| `collections.py` | `GET /api/collections` | List Qdrant collections (admin). |
| `system.py` | `GET /api/system[...]` | Version, model name, schema version. |
| `thumbnails.py` | `GET /thumb/{point_id}?w=…` | Pre-generated WebP thumbnails. 404-falls back to canonical. |
| `admin_index.py` | `GET /api/admin/index/...` | Indexer status, log, start/cancel. |

The `system` router also exposes the version banner and schema-version
negotiation handshake.

### Dev server

- **`search/dev_server.py`** — `python -m search.dev_server`. Flags:
  - `--no-model` — boot without loading SigLIP2 (~3 GB HF cache save).
  - `--demo-data` — boot an in-memory Qdrant collection seeded with N
    synthetic photos. Combine with `--no-model` to iterate on the UI
    without a GPU or a real library.

---

## `indexer/` — CLI

Runs on the GPU host, where the NAS is mounted and CUDA is available.
Has no FastAPI, no HTTP, no async — purely synchronous batch embedding.
The same `ghcr.io/isaaclb98/image-search:latest` image ships the
indexer as `python -m indexer.local_sync` (or `run_pipeline`) — invoke
it from the host's Python env, not as a container. The
`--profile indexer` service in `docker/docker-compose.yml` is the
optional containerized path.

### Entry points

- **`indexer/local_sync.py`** — `python -m indexer.local_sync`. The
  feature-rich CLI. Modes:
  - Full sync: walk sources, embed, upsert.
  - Change detection: diff a prior run, only re-embed changed files.
  - `--prune`: remove Qdrant points whose source files no longer exist.
  - Backfill: re-embed using a different model.
- **`indexer/run_pipeline.py`** — `python -m indexer.run_pipeline`.
  Thin wrapper around `IndexerPipeline`. No change detection, no prune,
  no backfill. Right shape for a desktop "Index this folder" button.

### Pipeline

- **`indexer/pipeline.py`** — `IndexerPipeline` orchestration. Phases:
  scan → load → fingerprint → embed → upsert.
- **`indexer/scan.py`** — directory walk + extension filter.
  `IMAGE_EXTENSIONS` is the canonical list.
- **`indexer/image_loader.py`** — decode (PIL + pillow-heif for HEIC/HEIF),
  rotate from EXIF, letterbox to the model's input resolution.
- **`indexer/fingerprints.py`** — `content_sha256` (byte-exact) and
  `dhash` (64-bit perceptual hash). Both are stored as flat payload
  fields; the ranker reads them after the vector search to drop
  duplicates.
- **`indexer/blurhash.py`** — LQIP placeholder encoder. Stored as
  `blurhash` payload field; the frontend decodes client-side.
- **`indexer/vision_encoder.py`** — SigLIP2 vision-encoder wrapper.
  Batches up to `INDEXER_BATCH_SIZE` (default 8, see `search/config.py`).
- **`indexer/upsert.py`** — builds the canonical `Payload` from
  per-file metadata + fingerprints + blurhash + model metadata, then
  upserts to Qdrant in batches.
- **`indexer/cache.py`** — SQLite cache of "what's already in Qdrant".
  Atomic writes, faster lookups, and the same public API
  (`load / save / has / add / remove_missing / rebuild_from_qdrant`).
- **`indexer/heal.py`** — runs a healing sweep over a collection to
  repair missing fields (e.g. older points without `_schema_version`).
- **`indexer/thumbnails.py`** — generates pre-baked WebP thumbnails
  (384×384, q80) at index time. The frontend's `?w=` request picks a
  size; the canonical file is always written.
- **`indexer/sync_meta.py`** — Option-B shim. Pre-September-2026
  this module also created the `images_pending` staging collection
  and the `_sync_meta` drift marker; that staging-collection
  design was deleted when the `--qdrant-collection` ambiguity
  caused every incremental index to silently re-embed every
  already-indexed file. Today the module only exposes a thin
  `ensure_sync_collections()` shim for test fixtures that pre-date
  the refactor; production code calls `upsert.ensure_collection`
  directly.

### Model registry

The indexer resolves `--model` to an `Embedder` via
`image_search_kernel.registry.get()`. Default variant is set by the
`SIGLIP_VARIANT` env var (defaults to `so400m/16-384`,
`ViT-so400m-patch16-384`, 1152-dim). For the full variant table see
`search/config.py:SIGLIP_VARIANTS`.

---

## `image_search_kernel/` — shared package

Imported by both `search/` and `indexer/`. Has no I/O of its own — the
goal is that any code path that needs a model constant, a payload
field name, or a vector primitive pulls it from here so the two
halves cannot drift.

- **`image_search_kernel/payload_schema.py`** — the canonical
  `Payload` TypedDict, the `SCHEMA_VERSION = 1` constant, and every
  `FIELD_*` string constant used in the Qdrant payload. The prose
  mirror is [`SCHEMA.md`](./SCHEMA.md).
- **`image_search_kernel/registry.py`** — `Model` dataclass,
  `Embedder` Protocol, `MockEmbedder` (deterministic, no weights),
  the `register()` decorator, and `get(name)`. Indexer and search
  both resolve models through this.
- **`image_search_kernel/vectors.py`** — vector primitives (unit-norm
  enforcement, batched ops). Used by both sides.
- **`image_search_kernel/qdrant_url.py`** — URL parsing for the
  `QDRANT_URL` env var. (Historically was shared across both sides;
  still pulled in by tests.)
- **`image_search_kernel/_real_models.py`** — lazy loader for the
  real SigLIP2 weights via `open_clip`. Only invoked on first
  `registry.get(name)` call after `torch` is imported. Safe to import
  in tests; the loader no-ops if torch isn't available.

---

## `frontend/` — SvelteKit 2 + Svelte 5 + TypeScript SPA

The SPA consumes the search JSON API. Type safety end-to-end:

1. FastAPI generates `openapi.json` at runtime.
2. `cd frontend && npm run gen:openapi` refreshes the pinned copy.
3. `npm run gen:types` emits TypeScript types.
4. `npm run gen:zod` validates hand-written Zod parsers against
   `openapi.json` (drift check).

### File-based routes

| Path | Purpose |
|---|---|
| `/` | Home — default search. |
| `/photo/[id]` | Single-photo detail page. |
| `/similar/[id]` | "More like this" landing. |
| `/random` | Random surf. |
| `/for-you` | Personal feed. |
| `/albums` | Album list. |
| `/albums/[id]` | Album detail. |
| `/albums/likes` | Favorites. |
| `/albums/dislikes` | Dislikes. |
| `/settings` | Settings + index status. |

### Generated / hand-rolled

- `frontend/openapi.json` — checked-in copy of the backend OpenAPI,
  used by the gen scripts and tests.
- `frontend/scripts/gen-openapi.mjs` — fetch live backend, write to
  `frontend/openapi.json`.
- `frontend/scripts/gen-types.mjs` — emit TS types from `openapi.json`.
- `frontend/scripts/gen-zod.mjs` — validate the hand-written Zod
  schemas in `frontend/src/lib/api/schemas.ts` against `openapi.json`.
  Drift fails CI.

### Tests

- `npm run test:unit` — Vitest unit + component tests.
- `npm run test:e2e` — Playwright against the live SPA. See
  `frontend/e2e/README.md` for the two-tier organization (fundamental
  = CI gate, exploratory = human triage).
- `npm run check` — `svelte-check` over the whole TS surface.

---

## Data flow: a search query, end to end

1. User types "sunset over mountains" in the SPA.
2. SPA `GET`s `/api/search?prompt=...` (debounced) via the typed
   client in `src/lib/api/`.
3. `search/routers/search.py` validates the query, calls the
   `diversity` service.
4. `search/diversity_compute.py`:
   - Calls `search/text_encoder.py` → SigLIP2 text encoder → variant-dim
     unit-norm query vector.
   - Calls Qdrant with `query_points(vector, limit=BIG, with_payload=True)`.
   - Drops exact (`content_sha256`) and near (`dhash` Hamming ≤ threshold)
     duplicates.
   - Re-orders by relevance + a freshness/quality score with a tunable
     relevance drop allowed for picking a more novel result.
5. `search/index_db.py` annotates each candidate with hot metadata
   (blurhash, folder, mtime) and joins with the user's favorites /
   dislikes sets.
6. Response JSON is shaped by `SearchResponse` Pydantic model.
7. SPA renders the grid. Click on a tile navigates to
   `/photo/[id]`, which lazy-fetches `/api/photo/{id}` for the full
   metadata panel and renders `<img src="/photo/{id}/raw">` for the
   full-resolution image.

---

## Stable pagination: frozen ranking snapshots

Plain `/api/search` does **not** pass `offset` through to Qdrant. It
freezes one ranking per query and slices pages out of it
(`search/search_snapshot.py`, driven by
`_indexed_helpers.materialize_search_page`).

Why: HNSW ranks `offset + limit` candidates to serve a page, and graph
breadth scales with that depth. Each page of one query is therefore
ranked at a *different* depth, and neighbouring pages disagree near
their boundary. Measured on 2.05M points, walking offsets 0→480 in
steps of 24 returned duplicate ids across pages (24 over 8 query
vectors) — with the indexer completely idle. This is depth drift, not
collection mutation; each request is individually correct, the pages
simply do not align.

The fix ranks at a **constant** depth: every band fetch uses
`offset=0` plus a `must_not has_id` exclusion of everything already
frozen, so the ranking never drifts and pages are disjoint by
construction.

Mechanics:

- The snapshot stores `(id, score)` only — not payloads. At the 20k cap
  that's ~800KB/entry. Payloads are hydrated per page via
  `retrieve_batch` (~2ms for 24 ids), which keeps favourite/dislike
  flags live rather than frozen for the TTL.
- The snapshot grows in **bands** (initial 256, then 500). Page 1 needs
  only the shallow initial band, so it stays ~15ms cold instead of
  paying for a deep upfront ranking (depth 5000 = 179ms, 10000 = 473ms).
- A cold band fetch costs ~0.5–1s once the exclusion set passes ~1750
  ids, so the next band is prefetched on a background daemon thread
  after a page is served — hiding the cost behind reading time. With
  prefetch on, a 40-page walk showed no page over 100ms, versus spikes
  of 540/419/692ms with it off.
- Growth stops at `SEARCH_MAX_SNAPSHOT_SIZE` (20k = 833 pages of 24);
  past that `has_more` goes false rather than degrading further.
- Extension is serialised by a per-snapshot lock so concurrent page
  requests (the SPA prefetches two viewports ahead) never fetch the same
  band twice.

Guaranteed: zero overlap between pages of one query. Approximate:
ordering *across* a band boundary — band 2 is "top 500 of what
remains", not a perfect continuation of one global ranking. Given the
flat score tail (rank 2000 = 0.7798, rank 10000 = 0.7481) that
reordering is not visible.

This cache is deliberately **separate** from `DiversityResultCache`,
which stores `DiversityStats` alongside its hits because it caches an
MMR re-ranking. Plain search does no re-ranking, so sharing that type
would mean fabricating stats and recoupling two independent features.

Diversity mode is unaffected — it already materialised a full ranked
list into its own TTL cache, which is why it never showed the bug.

---

## Thumbnail serving

`/thumb/{point_id}?w=…` returns a pre-baked WebP. The indexer writes a
canonical 384×384 file at index time (`indexer/thumbnails.py`); the
router accepts any `?w=` in [64, 384] and 404s if the requested size
doesn't exist on disk, so the browser falls back to the canonical. See
`search/routers/thumbnails.py`.
