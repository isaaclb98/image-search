# SCHEMA — Qdrant point payload

The canonical reference for every field stored on a Qdrant point by the
`indexer/` writer. The single source of truth for field names lives in
[`indexer/schema.py`](./indexer/schema.py) — this doc is the prose mirror
and is updated alongside it.

If you're adding a field, edit `indexer/schema.py` first, then update the
table below. Both surfaces stay in lockstep.

## Point identity

| Field | Type | Source | Notes |
|---|---|---|---|
| `id` | str (UUID) | `indexer/upsert.py:id_for` | Deterministic UUID5 over `f"{shard}::{path.as_posix()}"`; re-runs of the indexer produce the same id, so Qdrant upserts are idempotent. |

## File provenance

| Field | Type | Source | Notes |
|---|---|---|---|
| `path` | str (absolute) | `Path.resolve()` | Absolute filesystem path on the host that ran the indexer. |
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

1. Add a `FIELD_*` constant to `indexer/schema.py`.
2. Extend the `Payload` TypedDict in the same file.
3. Update `indexer/upsert.py:build_payload` to populate it.
4. Update this doc's table.
5. If the search side needs it, add a `from indexer.schema import FIELD_*`
   to the consumer module. **Do not** hard-code the string in the reader.

## Renaming / removing a field

Breaking change. The on-disk Qdrant collection still has the old key.
Coordinate a backfill or a fresh-collection migration before shipping.
