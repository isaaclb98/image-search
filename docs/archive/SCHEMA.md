# SCHEMA — Qdrant point payload

The canonical reference for every field stored on a Qdrant point by the
`indexer/` writer. The single source of truth for field names lives in
[`image_search_kernel/payload_schema.py`](./image_search_kernel/payload_schema.py) —
this doc is the prose mirror and is updated alongside it.

If you're adding a field, edit the kernel module first, then update the
table below. Both surfaces stay in lockstep.

## Schema versioning

Every point carries a `_schema_version` field. The current version is
`1`. New versions are introduced by the kernel's
`migrate_collection` helper (§A2 of `docs/backend-refactor-plan.md`),
which copies vectors and applies registered field transforms to
produce a new collection. Readers refuse unknown versions with a
typed error (HTTP 503 + structured JSON body).

## Point identity

| Field | Type | Source | Notes |
|---|---|---|---|
| `_schema_version` | int | `image_search_kernel/payload_schema.py:SCHEMA_VERSION` | Set to `1` for any point written by a versioned writer. Readers refuse unknown values. |
| `id` | str (UUID) | `indexer/upsert.py:id_for` | Deterministic UUID5 over `f"{shard}::{path.as_posix()}"`; re-runs of the indexer produce the same id, so Qdrant upserts are idempotent. |

## File provenance

| Field | Type | Source | Notes |
|---|---|---|---|
| `path` | str (absolute) | `Path.resolve()` | Absolute filesystem path on the host that ran the indexer. |
| `folder` | str (absolute) | `Path.parent.resolve()` | Parent directory of the source image. Top-level files (image directly in the source root) get `folder == source_root`; symmetric with nested files, no special case. Powers folder-browsing in the desktop product and folder-grouped hydration in the search-side cache. |
| `shard` | str | indexer arg | Empty string `""` for the default (un-sharded) indexer run. |
| `collection` | str | indexer arg | Logical library (`kpop`, `portrait`, `general`, …); Qdrant payload-indexed as `keyword` and used as a `MatchAny` filter on the search side. |

## LQIP placeholder

| Field | Type | Source | Notes |
|---|---|---|---|
| `blurhash` | str \| null | `indexer/blurhash.py:compute_blurhash` | None when the encoder failed (missing / corrupt / non-image). The client renders the placeholder only when this is a non-null string; null falls through to no-placeholder. |

## Diversity fingerprints

Stored as flat payload fields (not nested) so the ranker can read them
without descending into a sub-object. Both are intentionally not Qdrant
payload-indexed; the ranker reads them from candidates after the vector
search.

| Field | Type | Source | Notes |
|---|---|---|---|
| `content_sha256` | str \| null | `indexer/fingerprints.py:content_sha256` | Hex SHA-256 of the file bytes. Used by the ranker for byte-exact deduplication. None if the read failed. |
| `dhash` | str \| null | `indexer/fingerprints.py:dhash` | 64-bit perceptual hash as 16-char hex. Used by the ranker for near-duplicate detection across recompressions and small edits. None if the read failed. |

## Filesystem metadata

| Field | Type | Source | Notes |
|---|---|---|---|
| `mtime` | int | `Path.stat().st_mtime` | Unix seconds; used by the indexer to decide whether a point needs re-embedding. |
| `size` | int | `Path.stat().st_size` | Bytes. |

## Embedding provenance

| Field | Type | Source | Notes |
|---|---|---|---|
| `model_name` | str | indexer arg | The exact model identifier (e.g. `ViT-gopt-16-SigLIP2-384`). Points from different models are never compared. |
| `model_revision` | str | indexer arg | Model revision string. Stored alongside `model_name` so a model upgrade can be detected and the collection re-indexed. |
| `model_dim` | int | `image_search_kernel.registry.get(name).dim` | Vector dimension produced by the model that wrote this point. Self-describing — a backfilled migration can verify each point's vector length matches its recorded dim without consulting the registry. |

## Indexer timestamp

| Field | Type | Source | Notes |
|---|---|---|---|
| `indexed_at` | str (ISO-8601 UTC) | `datetime.now(timezone.utc).isoformat()` | When this specific point was last written. Distinct from `mtime` (file change) so a re-index of an unchanged file still bumps it. |

## Why flat?

Qdrant payload is stored as a flat JSON object (Qdrant allows nesting but
all our queries filter on top-level keys). Keeping the schema flat means
every field is addressable by a single string and the ranker's
`payload.get("content_sha256")` lookup doesn't need to know the
structure of nested objects.

## Adding a field (checklist)

1. Add a `FIELD_*` constant to `image_search_kernel/payload_schema.py`
   (and to its `__all__`, grouped by category).
2. Extend the `Payload` TypedDict in the same file with the matching
   key/type.
3. Update `indexer/upsert.py:build_payload` to populate it.
4. Update this doc's table.
5. If the search side needs it, add `from image_search_kernel.payload_schema import FIELD_*`
   to the consumer module. **Do not** hard-code the string in the reader —
   `search/` and `indexer/` must agree on field names because they share
   the kernel.

## Renaming / removing a field

Breaking change. The on-disk Qdrant collection still has the old key.
Coordinate a backfill or a fresh-collection migration before shipping.
