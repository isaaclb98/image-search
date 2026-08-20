# image-search

Self-hosted semantic image search. SigLIP2 embeddings → Qdrant → text/image
queries via a FastAPI backend and a SvelteKit frontend.

## Quick start (local dev)

```bash
# Terminal 1 — Qdrant
docker compose -f docker/docker-compose.yml up qdrant

# Terminal 2 — search backend (mounts your NAS into /nas)
NAS_IMAGES_PATH=/path/to/your/photos \
    docker compose -f docker/docker-compose.yml up search

# Terminal 3 — frontend
cd frontend && npm install && npm run dev
# open http://localhost:5173
```

See `docs/` for the v2 spec and the testing strategy.

## Architecture

- **search/** — FastAPI backend (~21K LoC, OpenAPI at `/openapi.json`).
- **indexer/** — CLI that scans a NAS path, embeds with SigLIP2, writes
  to Qdrant.
- **frontend/** — SvelteKit 2 + Svelte 5 + TypeScript SPA. OpenAPI is
  the source of truth; `scripts/gen-zod.mjs` keeps hand-rolled zod
  schemas in sync.
- **docker/** — Container builds for `search` and `frontend`.

## Tests

Four layers per `docs/image-search-v2-testing.md`:

1. `pytest tests/` — 481 backend tests
2. `pytest tests/test_openapi_stability.py` — 31 schema-drift tests
3. `cd frontend && npx vitest run` — 25 frontend contract/integration tests
4. `cd frontend && npx playwright test e2e/` — 14 end-to-end browser tests
