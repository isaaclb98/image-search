# image-search

Self-hosted semantic image search over a local photo library.

- **Embeddings:** SigLIP2 (open_clip `webli` pretrained).
- **Vector store:** Qdrant (local container in dev, HTTPS reverse proxy in prod).
- **Backend:** FastAPI, single container, gunicorn + uvicorn workers.
- **Frontend:** SvelteKit 2 + Svelte 5 + TypeScript SPA. Speaks to the backend over an OpenAPI-typed client.
- **Auth:** Single-user app login (bcrypt + itsdangerous-signed session cookie). Auto-disabled in dev when `AUTH_PASSWORD_HASH` is empty.
- **Side store:** SQLite `index.db` for folder metadata, favorites, dislikes, saved searches, album membership. Background-refreshed from Qdrant.

## Set up

### Production (one container, one port)

The image at `docker/Dockerfile.search` builds the SvelteKit SPA with `adapter-static`, bakes it in, and FastAPI serves both `/api/*` and the SPA from `:8000`.

```bash
export NAS_IMAGES_PATH=/path/to/your/photos   # bind-mounted read-only at /nas
docker compose up -d                          # brings up Qdrant + search
```

Open <http://localhost:8000>.

Data persists in a named Docker volume (`qdrant_data`). `docker compose down` keeps it; `docker compose down -v` wipes it.

### Local dev (faster iteration)

```bash
# Backend (loads SigLIP2 the first time, ~3 GB into HF cache)
python -m venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"                    # or: pip install -e ".[dev]"
docker run -p 6333:6333 qdrant/qdrant:v1.12.4 # vector DB
NAS_IMAGES_PATH=/path/to/your/photos \
QDRANT_URL=http://localhost:6333 \
    python -m search.dev_server

# Frontend (separate shell, HMR)
cd frontend && npm install && npm run dev
# http://localhost:5173
```

Two flags useful for UI work without a GPU or a real library:

```bash
python -m search.dev_server --no-model                     # mock encoder
python -m search.dev_server --no-model --demo-data --demo-count 500   # in-memory Qdrant + 500 synthetic photos
```

## Use it

### Index your library

The indexer CLI is the canonical entry point. Two shapes:

```bash
# Full sync + change-detection vs prior run. Idempotent — deterministic
# UUID5 per (shard, path). This is the right tool for a real library,
# run on the GPU host.
python -m indexer.local_sync --source /path/to/your/photos

# Thin wrapper around IndexerPipeline. No diff, no prune. Right shape
# for an "Index this folder" button in a desktop client.
python -m indexer.run_pipeline --source /path/to/folder
```

See `indexer/local_sync.py --help` for the full flag list (`--prune`, `--backfill`, `--dry-run`, ...).

### Query

Open the SPA and search by text (`"beach sunset"`) or jump from a result to its nearest neighbours. Other entry points:

- `/random` — walks the whole library in random order, no repeats per session.
- `/similar/{id}` — nearest neighbours of a given photo.
- `/albums` — favorites, dislikes, and saved searches as albums.
- `/api/photo/{id}` — JSON metadata for a point.
- `/photo/{id}/raw?w=1920` — original image, on-demand resize.

The frontend picks up new Qdrant points within `INDEX_DB_REFRESH_INTERVAL_SECONDS` (default 6h) via a background refresh. Force one now:

```bash
curl -X POST http://localhost:8000/api/cache/refresh
```

### Stop / restart

```bash
docker compose down      # stop, keep data
docker compose down -v   # stop, wipe data (forces a full reindex on next start)
```

## API

`http://localhost:8000/openapi.json` is the source of truth. CI enforces that the frontend's pinned snapshot is a subset of the live backend's spec — see `tests/test_openapi_stability.py` (6 contracts catching path/method drops and response-type narrowing).