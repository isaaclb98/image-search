# image-search

Self-hosted semantic image search over a local photo library.

- **Embeddings:** SigLIP2 (`ViT-gopt-16-SigLIP2-384`, open_clip `webli` pretrained, 1536-dim).
- **Vector store:** Qdrant (local container in dev, HTTPS reverse proxy in prod).
- **Backend:** FastAPI, single container, gunicorn + uvicorn workers.
- **Frontend:** SvelteKit 2 + Svelte 5 + TypeScript SPA. Talks to the backend over an OpenAPI-typed client.
- **Auth:** Single-user app login (bcrypt + itsdangerous-signed session cookie). Auth is
  implicitly disabled in dev — when `AUTH_PASSWORD_HASH` and `AUTH_SECRET_KEY` are empty,
  the middleware short-circuits. See `.env.example` for the real `AUTH_*` knobs.
- **Side store:** SQLite `index.db` for folder metadata, favorites, dislikes, saved searches, album membership. Background-refreshed from Qdrant after indexer runs.

## Production deployment (one-command setup)

For a complete stack with Qdrant bundled as a sidecar:

```bash
# 1. Set your photo library path
export NAS_IMAGES_PATH=/path/to/your/photos

# 2. Start everything
docker compose up -d

# 3. Open http://localhost:8000
```

This brings up:
- **Qdrant** (v1.12.4 vector database) with persistent storage
- **Search API + SPA** (single container)

Data is persisted in a named Docker volume (`qdrant_data`), so your embeddings survive container restarts.

To stop: `docker compose down`. Data is preserved.
To wipe and reindex: `docker compose down -v` (removes the volume).

---

## Architecture

Two runnable halves:

- **`search/`** — FastAPI app, the only thing the frontend talks to.
- **`indexer/`** — CLI that walks a NAS path, embeds with SigLIP2, writes points to Qdrant.

The shared kernel — model registry, payload schema, Qdrant URL helper, vector primitives, migration helper — lives in **`image_search_kernel/`** and is imported by both halves. See [`docs/adr/0001-shared-kernel-package.md`](docs/adr/0001-shared-kernel-package.md).

---

## Quick start (local dev, full stack via Docker)

The image is **single-container**: `docker/Dockerfile.search` builds the
SvelteKit SPA with `adapter-static`, bakes the output into the image,
and FastAPI serves both the JSON API and the SPA from the same port.
One container, one port (8000).

```bash
# 1. Point at your photo library. Path goes into the search container
#    read-only at /nas. On macOS/Linux that's a folder; on Windows
#    it's a drive letter or UNC path that Docker can bind.
export NAS_IMAGES_PATH=/path/to/your/photos

# 2. Bring up Qdrant + the search container (which also serves the SPA).
docker compose up --build

# 3. Open the UI.
#    SPA + API:           http://localhost:8000
#    OpenAPI schema:      http://localhost:8000/openapi.json
#    Qdrant (direct):     http://localhost:6333
```

Then, in a separate shell on the GPU host:

```bash
# 4. Index your library (full sync, no diff). Repeats are safe —
#    upserts are idempotent (deterministic UUID5 per path).
python -m indexer.local_sync --source /path/to/your/photos
```

The frontend picks up new points within `INDEX_DB_REFRESH_INTERVAL_SECONDS`
(default 6h) via a background refresh. Force a rebuild now:

```bash
curl -X POST http://localhost:8000/api/cache/refresh
```

### Frontend-only dev with HMR

For fast iteration on the UI without rebuilding the SPA:

```bash
docker compose -f docker/docker-compose.yml up --build
```

This brings up three services:
- **qdrant** — vector database on `:6333`
- **search** — FastAPI + bundled SPA on `:8000`
- **frontend** — SvelteKit dev server with HMR on `:5173`

The frontend proxies `/api/*`, `/photo/*`, `/albums/*.zip` to the search
service inside the compose network. Open `http://localhost:5173` and
changes to `frontend/src/` hot-reload instantly.

## Quick start (local dev, without Docker)

For faster iteration on the backend or the model loading path:

```bash
# One-time setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Qdrant
docker run -p 6333:6333 qdrant/qdrant:v1.12.4

# Backend (loads SigLIP2 the first time — ~3 GB download into HF cache)
NAS_IMAGES_PATH=/path/to/your/photos \
QDRANT_URL=http://localhost:6333 \
    python -m search.dev_server

# Frontend (separate shell)
cd frontend
npm install
npm run dev
# http://localhost:5173
```

The dev server has a couple of flags useful for UI work without a GPU or
a real library:

```bash
# Skip the model entirely; serve a deterministic mock encoder.
python -m search.dev_server --no-model

# Boot with an in-memory Qdrant collection seeded with N synthetic
# photos — enough to exercise infinite scroll, no NAS needed.
python -m search.dev_server --no-model --demo-data --demo-count 500
```

## Indexer

The indexer has two entry points:

- **`python -m indexer.local_sync`** — feature-rich CLI. Modes: full sync
  (`--source`), change-detection vs prior run, prune (`--prune`),
  backfill. See `indexer/local_sync.py` for the full argument list.
  This is the right tool for a real library, on the GPU host.

- **`python -m indexer.run_pipeline`** — thin wrapper around the
  `IndexerPipeline`. No change detection, no prune, no backfill. This is
  the right shape for an "Index this folder" button in a desktop
  client (no prior state to diff against).

Both write the canonical payload defined by
[`image_search_kernel/payload_schema.py`](image_search_kernel/payload_schema.py).
Upserts are idempotent: the point id is a deterministic UUID5 over
`(shard, absolute path)`, so re-running the indexer does not duplicate
points. The point's `_schema_version` field is set on every write and
checked on every read — see [`docs/adr/0002-schema-versioning.md`](docs/adr/0002-schema-versioning.md).

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Browser (SPA)                              │
│                  SvelteKit 2 · Svelte 5 · TypeScript                    │
│                                                                         │
│   /, /search, /photo/[id], /similar/[id], /random, /for-you,           │
│   /albums, /albums/[id], /albums/likes, /albums/dislikes, /login        │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │  HTTP/JSON
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         search/  (FastAPI, gunicorn)                    │
│                                                                         │
│  /api/search · /api/similar/{id} · /api/random · /api/for-you/*         │
│  /api/discover/* · /api/favorites · /api/dislikes · /api/albums/*       │
│  /api/centroids · /api/centroids/{name}/search · /api/centroids/reload  │
│  /api/saved-searches · /api/collections · /api/photo/{id}              │
│  /photo/{id}/raw · /favorites/download.zip · /albums/{id}/download.zip  │
│                                                                         │
│  Reads Qdrant for vectors, IndexDB (SQLite) for hot metadata.           │
│  Path liveness check at read time so deleted files disappear fast.      │
└────────────┬───────────────────────────────────────────┬───────────────┘
             │                                           │
             ▼                                           ▼
┌─────────────────────────────┐               ┌──────────────────────────┐
│   Qdrant   (vector store)   │               │  IndexDB  (SQLite)        │
│   collection: images        │               │  folder / favorites /     │
│   dim: 1536, cosine          │               │  dislikes / saved         │
│   payload schema:            │               │  searches / albums        │
│   `image_search_kernel/      │               │                           │
│   payload_schema.py`         │               │                           │
└────────────▲────────────────┘               └────────────▲─────────────┘
             │                                           │
             │ writes                                    │ background
             │                                           │ refresh
             │                                           │
┌────────────┴───────────────────────────────────────────┴───────────────┐
│                          indexer/  (CLI on GPU host)                    │
│                                                                         │
│   walk → scan → load (PIL + pillow-heif + blurhash) →                   │
│   fingerprint (content SHA-256 + dHash) →                               │
│   embed (SigLIP2, batched) → upsert (Qdrant)                            │
└─────────────────────────────────────────────────────────────────────────┘
```

The shared layer (`image_search_kernel/`) is imported by both halves and
contains no I/O of its own.

## API surface

The full OpenAPI is generated at runtime from the FastAPI app and is the
single source of truth for the wire format:

- Dev: <http://localhost:8000/openapi.json>
- The frontend's typed client (`src/lib/api/`) is regenerated against
  this file via `cd frontend && npm run gen:openapi && npm run gen:types`.

The hand-written Zod schemas in `frontend/scripts/gen-zod.mjs` cover the
~8 endpoint shapes the SPA actually parses at runtime, and are validated
against `openapi.json` on CI to catch schema drift. They are intentionally
not auto-generated — see the header of that file for why.

## Tests

Three layers, each independent:

| Layer | Command | What it covers |
|---|---|---|
| Backend | `pytest tests/` | ~720 backend tests across 59 files (unit + integration + API contract, including parameterized cases). |
| OpenAPI drift | `pytest tests/test_openapi_stability.py` | 31 hand-curated invariants on the generated OpenAPI shape. |
| Frontend unit / contract | `cd frontend && npm run test:unit` | 27 Vitest tests across 3 files (typed client, schema parsers, primitives). |
| End-to-end browser | `cd frontend && npm run test:e2e` | 14 Playwright tests against the live SPA + search backend. |

`pytest` reads its config from `[tool.pytest.ini_options]` in
`pyproject.toml` (`asyncio_mode = "auto"`, `testpaths = ["tests"]`).

Lint:

```bash
# Backend
ruff check .

# Frontend
cd frontend && npm run lint
```

## Repo layout

```
.
├── search/                FastAPI app (~11K LoC)
│   ├── app.py             create_app(), middleware, /api/photo, /photo/raw, ZIPs
│   ├── routers/           One file per resource group (albums, favorites, …)
│   ├── *_compute.py       Pure-function side of the ranker; unit-tested in isolation
│   ├── index_db.py        SQLite hot-metadata store
│   ├── lazy_index_cache.py B5 lazy + stale-while-revalidate wrapper
│   └── dev_server.py      `python -m search.dev_server` entry point
│
├── indexer/               CLI (~3K LoC)
│   ├── local_sync.py      Feature-rich CLI (change-detect, prune, backfill)
│   ├── run_pipeline.py    Thin wrapper for "Index this folder" desktop action
│   ├── pipeline.py        IndexerPipeline orchestration
│   ├── upsert.py          build_payload + Qdrant upsert
│   ├── cache.py           SQLite "what's already in Qdrant" cache
│   ├── scan.py            Walk + filter
│   ├── image_loader.py    PIL / HEIF decode + letterbox resize
│   ├── vision_encoder.py  SigLIP2 wrapper (batch, device)
│   ├── fingerprints.py    content_sha256, dHash
│   └── blurhash.py        LQIP placeholder encoder
│
├── image_search_kernel/   Shared, no-I/O package (~1K LoC)
│   ├── payload_schema.py  Canonical field constants + Payload TypedDict
│   ├── registry.py        Model registry + Embedder Protocol
│   ├── vectors.py         L2 normalize / mean / cosine (pure Python)
│   ├── qdrant_url.py      QDRANT_URL → QdrantClient kwargs
│   └── migrate.py         Schema-version migration helper
│
├── frontend/              SvelteKit 2 + Svelte 5 + TypeScript SPA
│   ├── src/routes/        File-based routes (see list above)
│   ├── src/lib/api/       Typed client (regenerated via npm run gen:*)
│   ├── scripts/           gen-openapi, gen-types, gen-zod
│   └── e2e/               Playwright specs
│
├── tests/                 Pytest (~15K LoC, ~630 tests across 59 files)
├── docs/
│   └── adr/               Architecture decision records
├── docker/
│   ├── Dockerfile.search  Search container build
│   └── docker-compose.yml Qdrant + search + frontend (indexer runs on host)
├── data/                  Tiny reference fixtures (NOT user data)
├── .env.example           All env vars, with comments
└── pyproject.toml         uv-friendly, hatchling build backend
```

## Documentation

- [`docs/adr/`](docs/adr/) — architecture decision records (0001–0006).
- [`.env.example`](.env.example) — every environment variable, with rationale.

## License

MIT. See [`LICENSE`](LICENSE).
