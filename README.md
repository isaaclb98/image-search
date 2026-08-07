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

## Development

### Design system

The UI runs on **Tailwind v4** (standalone CLI, vendored) + **DaisyUI v5** (vendored CSS, no `@plugin`). Themes are DaisyUI's built-in `light`/`dark` re-skinned via CSS-variable overrides — see `search/static/css/input.css` for the design tokens (`--color-*`, type scale, shadow tier) and the `[data-theme=...]` re-skin block.

Build the bundle whenever you touch `input.css`:

```bash
./bin/tailwindcss -i search/static/css/input.css -o search/static/css/app.css
```

Or watch during editing:

```bash
./bin/tailwindcss -i search/static/css/input.css -o search/static/css/app.css --watch
```

CI runs `tw:build` before tests (`.github/workflows/dev-build.yml`), so a stale `app.css` won't pass review.

### Reusable state macros

`search/templates/_macros.html` ships four Jinja macros used across the page templates:

- `page_header(title, count=, count_label=, count_plural_label=)` — sticky `<h1>` + tabular-numeral count on the right.
- `empty_state(icon=, title=, body=, action_url=, action_label=)` — centered placeholder used when a list is empty.
- `error_state(message)` — DaisyUI `alert alert-error` for unhandled errors.
- `loading_skeleton(kind=)` — shimmering placeholder for `grid` or `detail` shapes.
- `blurhash_thumb(href, src, alt=, blurhash=, score=, score_str=, photo_id=, path=)` — LQIP thumbnail wrapper carrying the photo/lightbox data attributes.

`albums`, `saved`, `favorites`, `random`, `centroids`, `photo`, `album_detail`, and `discover_liked` opt into these at the right points. To extend the set, add a usage test in `tests/test_states.py`.

### Theme system

`search/templates/base.html` carries the Alpine-driven toggle (single button, dynamic `:aria-label` + `x-show` switching the sun/moon icon, `localStorage` persistence under key `theme`), a responsive mobile navigation drawer, the photo lightbox shell, and the keyboard-shortcuts help panel. The inline `<script>` at the top reads `localStorage` / `prefers-color-scheme` and sets `data-theme` on `<html>` *before* first paint to prevent FOUC. `search/static/js/ui.js` wires the shared interactions.

### Blurhash / LQIP

Indexed photos get a Blurhash LQIP computed at index time and stored in the Qdrant payload (`indexer/blurhash.py`). `blurhash>=1.0` is the only extra runtime dep. The browser decoder (`search/static/js/lib/blurhash.js`) paints a canvas placeholder and cross-fades to the real image when it arrives.

Backfill a collection without re-embedding:

```bash
python -m indexer.indexer /path/to/photos --qdrant-url http://localhost:6333 --reblurhash
```

Use this after upgrading the encoder, or for any point indexed before the `--reblurhash` feature shipped (those have `payload.blurhash == null` until the walk touches them). The walk is cursor-paginated, idempotent (a point whose current hash matches the recompute is skipped), and never re-embeds — it only writes the `blurhash` payload field.

### Test layout

- `tests/test_search_api.py` — full-stack FastAPI + in-memory Qdrant, covers `/api/search`, `/photo/{id}/similar`, etc.
- `tests/test_albums_api.py` / `tests/test_saved_searches_api.py` / `tests/test_favorites_api.py` — CRUD + ZIP round-trip.
- `tests/test_blurhash.py` — encoder edge cases + `build_payload` round-trip.
- `tests/test_states.py` — macro rendering + per-page opt-in guards.
- `tests/test_theme.py` — base.html / input.css file-content + Alpine-wiring checks.
- `tests/test_ui_interactive.py` — shared shell, lightbox, blurhash, favorite, and responsive UI contracts.
- `tests/test_indexer.py` — indexer CLI + payload schema (incl. `blurhash`).

The full suite is `<1m` on a laptop (CPU only). The in-memory Qdrant `location=":memory:"` is set via the `QDRANT_URL=memory://` env var in CI.
