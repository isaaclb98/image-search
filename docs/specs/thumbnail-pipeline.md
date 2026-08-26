# Thumbnail Pipeline Spec

**Date:** 2026-08-25
**Status:** Draft
**Author:** Isaac + Hermes

---

## Context

The web app serves full-resolution images for grid tiles. At ~2M photos this is a serious perf problem — every grid view pulls megabytes of data the browser immediately downscales.

We need a thumbnail pipeline that:
- Is generated at index time (no backfill, no lazy generation)
- Is small enough that storage is reasonable at 2M scale
- Is fast to encode so indexing isn't bottlenecked on it
- Works across all browsers (no AVIF-only assumptions)

---

## Format & Size

**WebP, 256×256 max dimension, quality 50.**

Rationale:
- WebP is 30-40% smaller than JPEG at equivalent quality, universally supported in browsers, and Pillow's encoder is fast (~25ms/image).
- AVIF would save another ~2KB/image but encodes at ~200ms/image — at 2M photos that's ~70 extra hours of generation for ~4GB savings. Not worth it.
- 256px is enough for grid tiles on any display up to 4K at the current 5-column layout. Lightbox continues to use `/photo/{id}/raw?w=1920` directly.
- Quality 50 is the sweet spot: ~8KB/image average, visually acceptable for grid view, not good enough for close inspection (which is fine — that's the lightbox's job).

**At 2M photos: ~16GB total thumbnail storage, ~14 hours generation overhead.**

---

## Generation

Thumbnails are generated **in the indexer pipeline**, same pass as embeddings + blurhash + fingerprints. One extra PIL operation per image, ~25ms overhead.

The indexer already opens the image, resizes to model resolution (256 or 384), and runs inference. The thumbnail is extracted as a side effect of that same decode:

```python
def compute_thumbnail(path: Path, out_dir: Path, point_id: str) -> Path:
    """Generate a 256px WebP thumbnail. Returns the output path."""
    img = Image.open(path)
    img.thumbnail((256, 256), Image.LANCZOS)
    out_path = thumbnail_path(out_dir, point_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "WEBP", quality=50, method=4)
    return out_path
```

The thumbnail path is deterministic from the point ID so the search side can compute it without a DB lookup.

---

## Storage Layout

```
{THUMBNAIL_DIR}/
  {prefix}/                    # first 2 chars of point_id (256 buckets)
    {point_id}.webp
```

Example: point ID `a3f8c1...` → `{THUMBNAIL_DIR}/a3/a3f8c1....webp`

Two-level prefix avoids putting 2M files in one directory (filesystem lookup degrades past ~100K files per directory on ext4/XFS). ~8K files per bucket at 2M scale.

`THUMBNAIL_DIR` is a new env var, defaulting to `/app/data/thumbnails`. The docker-compose `search_data` volume already mounts `/app/data`, so thumbnails persist alongside the SQLite index and HF model cache.

---

## Serving

New endpoint on the search side:

```
GET /thumb/{point_id}
```

- Returns the WebP file with `Content-Type: image/webp`
- `Cache-Control: public, max-age=31536000, immutable` (thumbnails are content-hashed by point ID, never change unless reindexed)
- 404 if the thumbnail doesn't exist (unindexed photo)

The endpoint is a thin FileResponse lookup — compute the path from the point ID, check existence, serve. No Qdrant round-trip needed.

The search side mounts the same `search_data` volume (already done in docker-compose.yml) so it can read thumbnails the indexer wrote.

---

## Frontend Changes

PhotoTile requests `/thumb/{id}` as its primary image source:

```svelte
<img
  src="/thumb/{pointId}"
  onerror={() => { /* fall back to blurhash */ }}
/>
```

On 404 (thumbnail missing), fall back to the existing blurhash decode. This keeps cold-start grid rendering functional while thumbnails are being generated for a new library.

Lightbox is unchanged — it uses `/photo/{id}/raw?w=1800` for full quality.

The grid's virtual scrolling already renders ~100-150 tiles at a time. With WebP thumbnails at 8KB each, a full screen of tiles is ~1.2MB total vs. ~100MB+ for raw JPEGs.

---

## Docker/Compose Changes

- `docker-compose.yml`: `search_data` volume already covers `/app/data/thumbnails`. No changes needed.
- Indexer needs write access to the same volume. For the dev workflow (indexer runs on host, search runs in Docker), mount the host's thumbnail directory into the container.
- Add `THUMBNAIL_DIR` to `.env.example`.

---

## Out of Scope

- **Backfill for existing libraries.** We're starting fresh. If backfill is needed later, a separate script can be written.
- **Multiple sizes.** One size covers the grid. Lightbox uses dynamic resize via `?w=N`.
- **AVIF.** Not worth the encode cost. Can revisit if libsvt-av1 or aom get fast enough.
- **Thumbnail invalidation.** Thumbnails are tied to point IDs (which are `sha1(path)`). If a photo is reindexed with the same path, the point ID is the same and the old thumbnail is overwritten. No explicit invalidation needed.

---

## Implementation Order

1. `compute_thumbnail()` function in `indexer/thumbnails.py`
2. Wire into indexer pipeline (`pipeline.py`) — call after embedding, before Qdrant upsert
3. `GET /thumb/{point_id}` endpoint on search side (`routers/thumbnails.py`)
4. PhotoTile update: use `/thumb/{id}` with blurhash fallback
5. Update `docker-compose.yml` comments to mention thumbnail volume
