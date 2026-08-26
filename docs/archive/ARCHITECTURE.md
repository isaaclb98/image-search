# Architecture

Module-by-module reference for the three runnable halves (`search/`,
`indexer/`, `image_search_kernel/`) and the SvelteKit frontend.

For the *why* behind each decision, see
[`backend-refactor-plan.md`](./backend-refactor-plan.md) and the ADRs
in [`adr/`](./adr/). For the wire-format side of the API, see the
generated OpenAPI at `/openapi.json` (dev: <http://localhost:8000/openapi.json>).

---

## `search/` — FastAPI app

The search side is a pure JSON API. It does not serve HTML, it does not
host the SPA build. The SvelteKit frontend talks to it over HTTP/JSON
and proxies through SvelteKit's server-side fetch in production.

### App wiring

- **`search/app.py`** — `create_app()` factory. Wires the Qdrant client,
  IndexDB, middleware (auth, CORS, logging), the static mount for SPA
  fallback, and every router from `search/routers/`. Also owns the
  `/api/photo/{point_id}` JSON endpoint, the `/photo/{id}/raw` image
  streamer (with cache headers and path-liveness checks), and the two
  streaming ZIPs (`/favorites/download.zip`, `/albums/{id}/download.zip`).
- **`search/config.py`** — env-var parsing and `AppConfig` dataclass.
  Reads from process env (which `.env` populates via `python-dotenv`).
- **`search/middleware.py`** — request logging, CORS, session-cookie
  attach, and the auth gate for non-public routes.
- **`search/auth.py`** — bcrypt password hashing + itsdangerous-signed
  session cookie. Single-user app login; see `AUTH_*` in `.env.example`.
- **`search/qdrant_client.py`** — async wrapper around the Qdrant
  client (see [ADR-0006](./adr/0006-async-qdrant-client.md)).
- **`search/text_encoder.py`** — SigLIP2 text encoder wrapper. Mirrors
  the indexer's vision encoder so queries and points live in the same
  space. Returns unit-norm vectors.

### Dual store: Qdrant + IndexDB

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
  See [ADR-0005](./adr/0005-lazy-cache-refresh.md).

- **`search/image_resolver.py`** — turns a stored absolute path into
  the public `/photo/{id}/raw` URL the frontend embeds. Honors the
  optional `PATH_PREFIX` → `NAS_IMAGES_BASE` rewrite for cross-machine
  setups (index on Windows, serve on Linux).

### Ranking & feature compute

The big read-side features follow a `compute.py` ↔ service-module split
from [phase B3](./backend-refactor-plan.md):

- **`search/diversity.py`** + **`search/diversity_compute.py`** — applies
  byte-exact and perceptual-hash deduplication plus a relevance-drop
  MMR pass to ordinary search results. Tunable via
  `DIVERSITY_*` env vars.
- **`search/centroids.py`** + **`search/centroids_compute.py`** —
  loads `.pt` centroid files emitted by `isaac-image-scoring`'s
  `extract` command, serves them as query vectors. Mutually exclusive
  with text prompts.
- **`search/discover.py`** + **`search/discover_compute.py`** — the
  Discover rabbithole. Burst-based seed-then-recommend sampling with
  MMR diversity inside the burst pool. See `DISCOVER_*` env vars.
- **`search/for_you.py`** + **`search/for_you_compute.py`** — a
  personal feed scored from the user's favorites/dislikes centroid.
- **`search/centroids_compute.py`** etc. are **pure functions** —
  vectors in, vectors/masks out, no I/O, no globals, no logging beyond
  debug invariants. Unit-tested in isolation; the I/O module is a thin
  orchestrator on top.

### Routing

One file per resource group in `search/routers/`. Each exports a
`build_<name>_router(...)` factory that returns an `APIRouter`; the
factory pattern lets tests inject mock dependencies.

| File | Routes | Notes |
|---|---|---|
| `search.py` | `GET /api/search` | Text + image + filename filter. The hot path. |
| `similar.py` | `GET /api/similar/{point_id}` | "More like this" by Qdrant Recommend. |
| `random.py` | `GET /api/random` | Uniformly random, optional folder bias. |
| `for_you.py` | `GET /api/for-you/{state,feed}`, `POST /api/for-you/reset` | Personal feed. |
| `discover.py` | `POST /api/discover/{start,pick}` | Two-image pick rabbithole. |
| `favorites.py` | `POST/GET/DELETE /api/favorites[...]` | Per-photo favorite toggle + list + ZIP. |
| `dislikes.py` | `POST/GET/DELETE /api/dislikes[...]` | Per-photo dislike + list. |
| `albums.py` | `POST/GET/PATCH/DELETE /api/albums[...]` | Named user-curated sets; ZIP download. |
| `saved_searches.py` | `POST/GET/DELETE /api/saved-searches[...]` | Named search recipes. |
| `centroids_list.py` | `GET /api/centroids` | List loaded centroids. |
| `centroids_search.py` | `GET /api/centroids/{name}/search` | Search with a centroid as the query vector. |
| `centroids.py` | `POST /api/centroids/reload` | Re-scan the centroids dir without restarting. |
| `collections.py` | `GET /api/collections` | List Qdrant collections (admin). |
| `system.py` | `GET /api/system[...]` | Version, model name, schema version. |

The `system` router also exposes the version banner and schema-version
negotiation handshake.

### Dev server

- **`search/dev_server.py`** — `python -m search.dev_server`. Flags:
  - `--no-model` — swap in the deterministic mock text encoder; skip
    the SigLIP2 download entirely.
  - `--demo-data` — boot an in-memory Qdrant collection seeded with N
    synthetic photos. Combine with `--no-model` to iterate on the UI
    without a GPU or a real library.

---

## `indexer/` — CLI

Runs on the GPU host, where the NAS is mounted and CUDA is available.
Has no FastAPI, no HTTP, no async — purely synchronous batch embedding.

### Entry points

- **`indexer/local_sync.py`** — `python -m indexer.local_sync`. The
  feature-rich CLI. Modes:
  - Full sync: walk sources, embed, upsert.
  - Change detection: diff a prior run, only re-embed changed files.
  - `--prune`: remove Qdrant points whose source files no longer exist.
  - Backfill: re-embed using a different model.
- **`indexer/run_pipeline.py`** — `python -m indexer.run_pipeline`.
  Thin wrapper around `IndexerPipeline`. No change detection, no prune,
  no backfill. The right shape for a desktop "Index this folder"
  button.

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
  Batches up to `INDEXER_BATCH_SIZE` (default 16 on a 24 GB GPU).
- **`indexer/upsert.py`** — builds the canonical `Payload` from
  per-file metadata + fingerprints + blurhash + model metadata, then
  upserts to Qdrant in batches.
- **`indexer/cache.py`** — SQLite cache of "what's already in Qdrant",
  replaces the prior JSON implementation (phase B4). Atomic writes,
  faster lookups, and the same public API (`load / save / has / add /
  remove_missing / rebuild_from_qdrant`).
- **`indexer/heal.py`** — runs a healing sweep over a collection to
  repair missing fields (e.g. older points without `_schema_version`).
- **`indexer/migrate_source_from_path.py`** — one-shot tool for
  re-pointing existing points at a new filesystem path.

### Model registry

The indexer resolves `--model` to an `Embedder` via
`image_search_kernel.registry.get()`. The default
`ViT-gopt-16-SigLIP2-384` (open_clip `webli` pretrained) is the only
production model today. See [ADR-0003](./adr/0003-model-registry.md)
for how to add another.

---

## `image_search_kernel/` — shared package

Imported by both `search/` and `indexer/`. Has no I/O of its own — the
goal is that any code path that needs a model constant, a payload
field name, or a vector primitive pulls it from here so the two
halves cannot drift.

- **`image_search_kernel/payload_schema.py`** — the canonical
  `Payload` TypedDict, the `SCHEMA_VERSION = 1` constant, and every
  `FIELD_*` string constant used in the Qdrant payload. The
  prose mirror is [`SCHEMA.md`](../SCHEMA.md). See
  [ADR-0002](./adr/0002-schema-versioning.md).
- **`image_search_kernel/registry.py`** — `Model` dataclass,
  `Embedder` Protocol, `MockEmbedder` (deterministic, no weights),
  the `register()` decorator, and `get(name)`. Indexer and search
  both resolve models through this.
- **`image_search_kernel/vectors.py`** — `l2_normalize`,
  `mean_vector` (with per-vector weights), `cosine`. Pure NumPy /
  Torch, no I/O.
- **`image_search_kernel/qdrant_url.py`** — `client_kwargs()` turns
  a `QDRANT_URL` env var into a QdrantClient kwargs dict (host vs.
  URL detection, optional API key).
- **`image_search_kernel/migrate.py`** — `migrate_collection()` —
  copies vectors and applies registered field transforms to promote
  a collection from one `_schema_version` to another.

See [ADR-0001](./adr/0001-shared-kernel-package.md) for the rationale.

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
| `/login` | App login (single-user, bcrypt + signed cookie). |
| `/photo/[id]` | Single-photo detail page. |
| `/search` | Saved-search landing. |
| `/similar/[id]` | "More like this" landing. |
| `/random` | Random surf. |
| `/for-you` | Personal feed. |
| `/albums` | Album list. |
| `/albums/[id]` | Album detail. |
| `/albums/likes` | Favorites. |
| `/albums/dislikes` | Dislikes. |

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

- `npm run test:unit` — Vitest unit + component tests (27 tests across
  3 files: typed client, Zod schema parsers, primitives).
- `npm run test:e2e` — Playwright (14 tests against the live SPA).
- `npm run check` — `svelte-check` over the whole tree.

---

## Data flow: a search query, end to end

1. User types "sunset over mountains" in the SPA.
2. SPA `POST`s to `/api/search?prompt=...` (debounced) via the typed
   client in `src/lib/api/`.
3. `search/routers/search.py` validates the query, calls the
   `diversity` service.
4. `search/diversity_compute.py`:
   - Calls `search/text_encoder.py` → SigLIP2 text encoder → 1536-dim
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
