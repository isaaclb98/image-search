# Design — `isaac-image-search`

> Big-picture design document. Implementation details live elsewhere; this file describes the *shape* of the system, the *why*, and the boundaries.

---

## What this is

A personal image search engine. Given a folder of images on a NAS, embed each one with SigLIP2, store the embeddings in Qdrant, and let a text query (or an example image) retrieve the closest matches.

It is **not** a content-management system, a photo editor, a tagging tool, or a general-purpose vector DB demo. It is a single-purpose tool: turn a folder of images into something you can search by description.

---

## System topology

Three machines, three roles, one shared substrate:

```
┌────────────────────────────────────────┐
│  Windows                               │
│  (24GB GPU)                            │
│                                        │
│  ┌──────────┐    ┌──────────────────┐  │
│  │indexer.py│    │ search container │  │
│  │ (vision) │    │ (UI + text enc.) │  │
│  └────┬─────┘    └────────┬─────────┘  │
│       │                   │            │
└───────┼───────────────────┼────────────┘
        │ push              │ query
        ▼                   ▼
   ┌─────────────────────────────┐
   │   Qdrant (k8s)              │
   │                             │
   │   only shared component     │
   └─────────────────────────────┘
```

| Machine      | Role                  | Always on? | Talks to Qdrant? |
|--------------|-----------------------|------------|------------------|
| Windows      | Bulk embedding + search UI (Docker) | No — on-demand | Yes, both push and query |
| k8s cluster  | Qdrant (always-on store) | Yes | Self |

The Windows machine hosts both the indexer script and the search-side Docker container. K8s is a possible future home for the search container; the Optiplex is not in the picture for the runtime.

**Qdrant is the only shared component.** The indexer and the search side never talk to each other. They share data, not code.

## URL contract

Six routes, total. The search side is just a website.

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Search form + result grid. Optional `?q=...` carries the query, so links to results are real URLs. |
| `/photo/{id}` | GET | Single photo detail page. Real URL — open in new tab, bookmark, share, back button. |
| `/photo/{id}/raw` | GET | Raw image bytes (Content-Type sniffed from the file). The detail page's `<img src>` and the grid thumbnails all point here. |
| `/centroids` | GET | Custom-centroid list page. One card per loaded centroid, each linking into a centroid-anchored search. |
| `/api/search` | GET | JSON-only search endpoint. Powers the grid; also useful for scripting. Returns `{results: [{id, path, score}, ...]}`. |
| `/api/centroids` | GET | JSON list of loaded centroids (name, model, dim, source path, …). |
| `/api/centroids/{name}/search` | GET | Search using a loaded centroid as the query vector. Same response shape as `/api/search`. |
| `/api/centroids/reload` | POST | Rescan `CENTROIDS_DIR` and rebuild the in-memory store. No filesystem watcher. |
| `/api/collections` | GET | JSON list of distinct library (`collection` payload field) values with point counts. |

`/photo/{id}/raw` is **not** a pre-emptive add — it's load-bearing for the other three. The detail page can't render the full image, and the grid thumbnails can't lazy-load, without *some* way to translate a Qdrant payload path into HTTP-returnable bytes. We could mount the NAS into the container and serve from `/static/...`, but that leaks the storage layout into URLs. The sub-route keeps the URL opaque (id only) and treats storage as an implementation detail.

Optional, only if needed:
- `/api/photo/{id}` — JSON metadata for a single photo. Skip until something wants it.

That's the entire HTTP surface. Adding endpoints is easy; resist pre-emptively.

---

## Architectural philosophy

The system is built on five principles. Every design decision should be checkable against these.

### 1. The indexer is a translator, not a brain

It converts an image on disk into a Qdrant point. That's the whole job. It does not score, cluster, deduplicate perceptually, or make taste judgments. Those concerns live elsewhere (see [`isaac-image-scoring`](../isaac-image-scoring) for taste-based scoring).

If a design needs the indexer to be clever, the design is wrong.

### 2. One-way pipes only

The indexer writes to Qdrant. The search side reads from Qdrant. There is no callback, no event bus, no shared state outside Qdrant. Either side can be down, broken, or being rewritten without affecting the other.

### 3. Idempotent and resumable

Re-running the indexer on the same folder is safe. It skips what's already indexed (by file identity) and re-embeds what changed. A crashed run picks up where it left off.

### 4. Stateless on both ends

The indexer holds no state between runs. The search side holds no state about what's indexed. **Qdrant is the only source of truth** for "what exists in the system." If Qdrant forgets, the system is empty; no local caches hide this.

### 5. Boring infrastructure

Standard patterns. Standard libraries. No novel algorithms, no clever optimizations, no custom indexing structures. Qdrant does the indexing. PIL does the image loading. SigLIP2 does the embedding. FastAPI does the serving. Vanilla JS does the UI.

If something feels too clever, it's the wrong thing.

---

## Component responsibilities

### The indexer (`indexer.py`)

A standalone Python program. Reads images from a folder (NAS mount, local path, or list), embeds each with the SigLIP2 vision tower, upserts the resulting vector to Qdrant with a minimal payload.

**Does:**
- Read image files
- Apply EXIF orientation
- Letterbox-resize to model's native input size
- Embed with SigLIP2 vision tower (PyTorch, GPU)
- Upsert to Qdrant with payload (`path`, `id`, `mtime`, `size`, `model_version`)
- Skip files already indexed (by `id`)
- Print progress, count failures, exit non-zero on unfixable errors

**Does not:**
- Score aesthetics (that's `isaac-image-scoring`)
- Cluster, deduplicate perceptually, or analyze
- Serve queries
- Hold any state between invocations

**Runs on:** Windows (the only machine with a GPU).

### The search side

A lightweight thing — exact shape TBD — that runs on the Optiplex. Encodes a text query with the SigLIP2 text tower (CPU is fine, ~50–200ms per query), queries Qdrant, returns the top-K results.

The query path is the only thing that lives "in the stack." Everything else is a peripheral tool that produces or consumes Qdrant data.

### Qdrant

A single collection holds all image vectors. Payload fields are kept minimal at first (just `path` and identity) and grow as use cases demand. Schema decisions live in the Qdrant collection itself, not duplicated in client code.

### What is *not* a component

- **No central config service.** Configuration lives in code, env vars, or Qdrant payloads. No etcd, no consul, no vault.
- **No auth layer.** This is a single-user system on a private LAN. Qdrant sits behind the k8s cluster's existing network controls.
- **No k8s deployment for the search side** *in v1*. The container runs on Windows. K8s is a fine future home and explicitly not ruled out.
- **No event-driven indexing.** No filesystem watcher, no inotify, no message queue. The indexer is a batch job you run when you have new images.

---

## Relationship to `isaac-image-scoring`

These are sibling tools, not parent-child.

| | `isaac-image-scoring` | `isaac-image-search` |
|---|---|---|
| **Question it answers** | "Does this match my taste centroid?" | "What images match this query?" |
| **Embedding direction** | Taste centroid ↔ image | Image ↔ image, text ↔ image |
| **Output** | A single score per image | A ranked list of images |
| **Storage** | A single `.pt` centroid file | A Qdrant collection of vectors |
| **GPU need** | Yes (batch ranking) | Indexer yes; search side no |
| **Relationship to Qdrant** | None (yet) | Native |

**Shared code:** `load_image`, `letterbox_resize`, and the SigLIP2 model loader. These live in `isaac-image-scoring` and are imported from it. The shared code is <100 lines and stable; making `isaac-image-scoring` pip-installable is the right move over copying or vendoring.

**Coupling direction:** `isaac-image-search` depends on `isaac-image-scoring` (for utilities). `isaac-image-scoring` does not depend on `isaac-image-search`. If `isaac-image-search` disappears tomorrow, `isaac-image-scoring` is unaffected.

**Down the road:** `isaac-image-scoring` could *consume* from Qdrant instead of recomputing embeddings. That's a future optimization, not a current coupling.

---

## Deployment shape

- **Indexer:** A Python script you run on Windows when you have new images to embed. No daemon, no service, no scheduler. Triggered by hand (or by a future cron — but not now).
- **Search side:** A Docker container you run on Windows when you want to search. Three config values, that's it:
  - `QDRANT_URL` — where Qdrant lives (e.g., `http://qdrant.forgejo.svc.cluster.local:6333` for in-cluster, or `http://localhost:6333` for a port-forwarded dev instance)
  - `QDRANT_API_KEY` — Qdrant API key. Only required when the Qdrant instance has auth enabled. Set as an env var on both the search container and the indexer process. Mirrors the pattern in `rhizome`.
  - `NAS_IMAGES_BASE` — root path where the NAS images are mounted inside the container (e.g., a Windows path bind-mounted into the container, or a UNC path). Paths stored in Qdrant payloads are resolved relative to this.
  - `WEB_UI_URL` — URL the web UI is served on (default: `http://localhost:8000`)
  - K8s deployment is a future option; running locally on Windows is the v1 path.
- **Qdrant:** Always on, on the k8s cluster, configured by the existing gitops setup.

No CI. No container registry. No automated builds. The search-side Docker image is built locally with `docker build` and run with `docker run`. The indexer has no container at all — it's a script you invoke.

---

## What is intentionally not in scope

These are tempting but explicitly *not* part of this system. Mentioned so future-me doesn't drift.

- **Taste-based scoring.** Lives in `isaac-image-scoring`.
- **Perceptual deduplication.** Burst-shot clustering, near-duplicate detection. Could be a downstream tool that *reads* from Qdrant.
- **Cluster visualization.** UMAP/t-SNE projection, 2D taste maps. A UI concern, deferred.
- **Aesthetic-aware re-ranking.** Score-first, then vector-search, then combine. A composition problem for the search side, not a core feature.
- **Public-dataset mining.** LAION, CC3M, etc. A separate pipeline that *writes* to the same Qdrant collection. Out of scope until the basic loop works.
- **Multi-user / multi-tenant.** This is a single-user system. Adding auth, quotas, or sharing would change the shape of every component.
- **Mobile, web deploy, SaaS.** Not now, possibly not ever.

---

## Index cache and user state

Qdrant is the source of truth for photos: immutable embeddings plus minimal payload metadata (`id`, `path`, `shard`, `mtime`, `size`, `indexed_at`, model fields, and collection labels). The search-side SQLite database is not a second photo authority. It holds a rebuildable cache of that photo metadata so local features can sample and filter quickly without scanning Qdrant on every request.

The same SQLite file is also the source of truth for user state: favourites now, and future per-photo user data later. That half is not rebuildable from Qdrant. If the SQLite file is lost, the metadata cache can be rebuilt from Qdrant on startup, but favourites are lost. Back up the SQLite file if the user state matters.

The database lives on the local filesystem (`INDEX_DB_PATH`, default `./data/images.db`) and is created on first run. Startup performs a Qdrant scroll rebuild, preserving favourite columns for still-existing point ids and dropping rows for photos no longer present in Qdrant. A periodic refresh repeats the rebuild on a six-hour interval. This is intentionally boring, single-host infrastructure: SQLite, Qdrant `scroll()`, and no hosted database.

Heal is an indexer-side cold-path CLI. When `indexer.heal --apply` deletes orphan Qdrant points, the search-side SQLite cache can still contain those rows until the next startup or periodic refresh. That eventual-consistency window is acceptable for v0.5 because Qdrant remains the photo source of truth and the cache is explicitly rebuildable.

---

## Open questions

Held here so they don't get lost. Resolution belongs in conversation, not in code.

1. **Framework: FastAPI.** Default choice; resolves to whatever feels right when building, but FastAPI is the guess.
2. **Payload schema: minimum viable, grows on demand.** `path` and `id` for v1. Add EXIF, score, palette, etc. when a use case demands it. Resist pre-populating.
3. **Indexer trigger: manual.** A scheduled job (k8s CronJob, Windows Task Scheduler) only when the manual loop gets annoying.
4. **Model version: re-embed on upgrade.** One model version at a time. When a better model comes, re-embed the full collection in one pass. The complexity of carrying multiple model versions isn't worth it for a personal tool.

---

## Custom centroids

Custom centroids are pre-computed embedding vectors (typically a
"taste mean" across a curated set of photos) that the search side
loads from disk and uses as a query anchor. The point is to bridge
[`isaac-image-scoring`](../isaac-image-scoring) (which produces
centroids) with `isaac-image-search` (which queries by them) so
the curated taste models become reusable search primitives.

### How they get here

`isaac-image-scoring` extracts centroids and saves them to disk
as `.pt` files with a known schema: `centroid` (the vector), `name`,
`model`, `feature_dim`, `n_images`, `extracted_at`. The search
side reads these on startup from a directory specified by the
`CENTROIDS_DIR` env var. There is no write path on the search
side — centroids are owned by `isaac-image-scoring` and the search
app is a consumer.

### The model/dim guard

Every centroid must come from the same embedding space as the
indexed images, otherwise Qdrant cosine search returns garbage
(1536-dim centroid against a 4096-dim dino v3 collection, etc.).
At config load, the search side derives the expected `model` and
`feature_dim` from `MODEL_NAME` via a small compat table. A
centroid whose `model` or `feature_dim` doesn't match is skipped
at load time and logged with a clear warning that names the
offending file. The store never serves a mismatched centroid.

### How the user uses them

The `?centroid=<name>` query param on `/` and `/api/search`
swaps the text encoder's output for the centroid's precomputed
vector. The result grid is unchanged; the result-count header
reads "for centroid `<name>`". Centroid search is **mutually
exclusive with text prompts** — passing both returns a 400 with
a clear message. A user with a "wuxia female leads" centroid
gets wuxia results; a user with a "noir" centroid gets noir
results. The feature is composability-light on purpose: build
the centroid you want in `isaac-image-scoring`, then point at it.

### Design tension, named

`DESIGN.md` is explicit that the search side is a "thin
read-side thing" and that Qdrant is the only source of truth.
A centroid store breaks the second half — the centroids live
on disk, not in Qdrant — and turns the search side into
something that *reads* additional state. The justification is
that centroids are not "what's indexed" (which is owned by
Qdrant); they are an external artifact that the search side
reads. Same shape as `isaac-image-search` reading utilities
from `isaac-image-scoring`: shared code, one-way dependency.

### What's deferred

Centroids with multiple "ingredients" (text prompts mixed with
image references, with weights) are out of scope for v1 — the
centroid as a single precomputed vector is the only path.
Composability between centroids ("search with A but not B")
isn't either; the existing `?negatives=...` param already
serves the only common use case for that.

---

## Glossary

- **Embedding** — a fixed-length vector representing an image or text in the SigLIP2 feature space.
- **Point** — one vector + one payload, stored in Qdrant. One per image.
- **Indexing** — the act of taking an image and creating a Qdrant point from it.
- **Querying** — the act of finding the K nearest points to a query vector.
- **The stack** — Qdrant plus whatever thin read-side thing lives on the Optiplex. *Not* the indexer.
- **The translator** — the indexer. A one-way pipe, not a brain.


## Saved searches

Named prompt presets. The user types a (positives, negatives)
combo into the search bar, hits `Save current`, names it, and
the shape is stored under that name. Applying the saved search
later (via the dropdown on `/` or from the `/saved` index)
re-populates the chips in place and re-submits the form.

Only the prompt text is captured. View, centroid, favourites
filter, and result limits are session state and are
intentionally **not** part of the saved shape — they wouldn't
make sense to recall across sessions (the photo set changes,
centroids get reloaded, view is whatever the device wants).

Why no sharing / export / import: the saved search is a local
ergonomic tool, not a collaboration artifact. Cross-machine
sharing adds a sync story (and a conflict-resolution story) for
no clear win. Keep it local. Drop the feature before you
generalise it.

Why no in-place edit (PATCH): the saved shape is two prompt
lists under a name. Editing means either re-typing prompts
(clunky) or routing through the same Save UI to overwrite
(also clunky). Delete + recreate is simpler and matches how
the user actually thinks about it ("that one's wrong, save a
new one with the right prompts").
