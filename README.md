# image-search

Self-hosted semantic image search over your photo library.

## What it is

Embed your photos with SigLIP2, store the vectors in Qdrant, and search them by text, by similarity, or against custom embedding anchors. Includes favorites, albums, saved searches, and a feedback-driven discovery feed.

Two halves:

- `indexer/` — CLI to embed a photo library and push to Qdrant
- `search/` — FastAPI app that serves the UI and the JSON API

SigLIP2 (via `open_clip`) + Qdrant.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
# Qdrant (Docker)
docker run -p 6333:6333 qdrant/qdrant

# Index a photo library
python -m indexer.indexer /path/to/photos --qdrant-url http://localhost:6333

# Start the web app — defaults to 0.0.0.0:8000
python -m search.app
```

Then open <http://localhost:8000>.

For custom host/port, log level, auto-reload, or workers, run uvicorn directly:

```bash
uvicorn search.app:_build_default_app --factory --host 0.0.0.0 --port 8000
```

For a fully containerized setup (Qdrant + search app), see `docker/docker-compose.yml`. The indexer runs on the host where your photos and GPU live.

## Use

Routes:

- `/` — search results (text or centroid anchor)
- `/photo/{id}` — single photo with similarity neighbours
- `/favorites` — starred photos, downloadable as zip
- `/albums` and `/albums/{id}` — curated collections
- `/saved` — saved searches
- `/centroids` — custom embedding anchors (point `CENTROIDS_DIR` at `.pt` files)
- `/random` — random sample from the cache
- `/discover` — feedback-driven recommendation feed

JSON API at `/api/*`. Health check at `/healthz`.

## Configuration

See `.env.example` for the full list. The most useful knobs:

- `QDRANT_URL` — Qdrant endpoint
- `QDRANT_COLLECTION` — collection name (default `images`)
- `CENTROIDS_DIR` — directory of `.pt` embedding files for custom search anchors
- `HF_HOME` — override the SigLIP2 model cache

## Test

```bash
pytest tests/ -v
```

Tests use an in-memory Qdrant and a deterministic mock text encoder. No GPU or model download required.

