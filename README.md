# image-search

Self-hosted semantic image search over a local photo library.

- **Embeddings:** SigLIP2 (open_clip `webli` pretrained). Default variant
  is `B/16-256` (`ViT-B-16-SigLIP2-256`, 768-dim, 256px input) —
  smallest, fastest, runs on CPU. Other variants supported via
  `SIGLIP_VARIANT` env var: `L/16-256` (1024-dim), `gopt/16-384`
  (1536-dim), `so400m/16-384` (1152-dim, prod default) — see
  `search/config.py:SIGLIP_VARIANTS`.
- **Vector store:** Qdrant (local container in dev, HTTPS reverse proxy in prod).
- **Backend:** FastAPI, single container, gunicorn + uvicorn workers.
- **Frontend:** SvelteKit 2 + Svelte 5 + TypeScript SPA. Speaks to the backend over an OpenAPI-typed client.
- **Auth:** None. Deploy behind a reverse proxy (caddy auth, oauth2-proxy, tailscale, etc.) if access control is needed.
- **Side store:** SQLite `index.db` for folder metadata, favorites, dislikes, saved searches, album membership. Background-refreshed from Qdrant.
- **Thumbnails:** Pre-baked WebP at 384×384, q80. Generated at index time, served from `/thumb/{id}?w=…`. See `indexer/thumbnails.py`.

## Set up

The image at `ghcr.io/isaaclb98/image-search:latest` is the single
container you need. It runs FastAPI on `:8000`, serves the bundled
SvelteKit SPA from the same port, and embeds photos by spawning the
indexer as an in-container subprocess (so the indexer has no host
dependency — see "Index your library" below).

### Production (pull from ghcr)

```bash
export NAS_IMAGES_PATH=/path/to/your/photos   # bind-mounted read-only at /nas
docker compose up -d                          # brings up Qdrant + search
```

`docker-compose.yml` (in this repo) pulls `image-search:latest` from
ghcr, runs a Qdrant sidecar, bind-mounts your photo library at
`/nas:ro`, and exposes `:8000` on the host.

Two named volumes hold state across image updates:

- **`qdrant_data`** — the vector index + payloads.
- **`image-search_search_data`** — the SQLite side store (favorites,
  dislikes, albums, saved searches), the SigLIP2 model cache (HF_HOME),
  and the runtime thumbnail cache. **Without this volume, every image
  update wipes your library state.** `docker compose down` keeps both;
  `docker compose down -v` wipes both.

First start downloads the SigLIP2 model (~30 s one-off, then cached in
`image-search_search_data`). Open <http://localhost:8000>.

### Local dev (faster iteration)

```bash
# Backend (loads SigLIP2 the first time, ~3 GB into HF cache)
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
docker run -p 6333:6333 qdrant/qdrant:v1.19.0 # vector DB
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

Open the SPA, go to **Settings → Index**, and click **Start**. The
search container spawns the indexer as an in-container subprocess —
no host Python needed. Settings polls status every second and shows
live progress + a cancel button.

For incremental runs (the default), re-running the indexer only
embeds photos that changed since the last run. Qdrant upserts are
idempotent (deterministic UUID5 per photo path), so this is safe
to run repeatedly. The first run on a fresh library takes a while;
subsequent runs are usually seconds.

### Index from the host CLI (advanced)

If you want to run the indexer directly (e.g. on a different host
or with GPU access the search container doesn't have), two shapes:

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