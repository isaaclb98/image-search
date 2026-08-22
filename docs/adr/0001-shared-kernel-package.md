# ADR-0001: Shared kernel package for `search/` and `indexer/`

**Status:** Accepted.
**Date:** 2026-08 (during the backend-refactor plan).
**Deciders:** Isaac.
**Related:** `docs/backend-refactor-plan.md` §3, §4.1.

## Context

Before this refactor, both `search/` and `indexer/` independently imported from each other in three places (`indexer/local_sync.py`, `indexer/heal.py`, `indexer/migrate_source_from_path.py` all pulled `client_kwargs` from `search.qdrant_url`). The `search.qdrant_url` module existed solely to bridge this gap. The dependency direction was inverted: the indexer — which runs on the GPU host, has no FastAPI or HTTP — was coupled to the search package.

Two further couplings existed but were unaddressed:

- Three constants lived as module-level literals in two places: `VECTOR_DIM = 1536` in `indexer/upsert.py`, `_EMBED_DIM = 1536` in `indexer/vision_encoder.py` and `search/text_encoder.py`, and `SIGLIP_RESOLUTION = 384` in `indexer/image_loader.py`. A model swap was a multi-file grep-and-replace with no compile-time check.
- The Qdrant payload schema field names lived in `indexer/schema.py`, but every reader (in `search/`) hand-coded the field names. A typo on either side would silently mis-interpret the payload.

## Decision

Extract a new package `image_search_kernel/` that contains:

- `qdrant_url.client_kwargs()` — the URL → QdrantClient kwargs helper (moved verbatim from `search/qdrant_url.py`).
- `payload_schema` — canonical field constants (`FIELD_ID`, `FIELD_PATH`, `FIELD_FOLDER`, `FIELD_MODEL_DIM`, etc.), the `Payload` TypedDict, and the `SCHEMA_VERSION = 1` constant.
- `vectors` — L2 normalize, mean vector, cosine similarity. Pure-Python primitives with no I/O.
- `registry` — the model registry, the `Embedder` Protocol, and the `MockEmbedder` (see ADR-0003).
- `migrate` — the schema-version migration helper (see ADR-0002).

The kernel has no I/O of its own and no imports from `search/` or `indexer/`. Both consumer packages import from the kernel for shared concerns. The old `search/qdrant_url.py` and `indexer/schema.py` are deleted.

## Consequences

- **Positive:** The dependency direction `kernel ← search, kernel ← indexer` is enforceable. `tests/test_architecture.py:test_kernel_does_not_import_search_or_indexer` fails CI if the kernel ever pulls a consumer-side module.
- **Positive:** `indexer → search` is broken. `tests/test_architecture.py:test_indexer_does_not_import_search` enforces it.
- **Positive:** Adding a desktop product that uses the same payload contract + registry requires importing only `image_search_kernel` — not `search/`. This was the original motivation for the kernel.
- **Negative:** Code that lived in `search/qdrant_url.py` or `indexer/schema.py` had to update imports. Migration was a single PR; the affected files were 4 imports of `client_kwargs` and one test of `payload_field_names`. Tests caught each break.
- **Negative:** The kernel is now a new place to look for things. Documented in `image_search_kernel/__init__.py` and discoverable by `__all__`.

## Alternatives considered

- **Inline the helpers into the consumer packages.** Rejected: would leave the three hardcoded dim/resolution constants duplicated in two places (still a grep-and-replace on model swap).
- **Make `search/` the kernel.** Rejected: `search/` carries FastAPI, HTTP, and Qdrant client construction. The kernel cannot depend on those; the desktop product wants the kernel without them.
- **Generate the kernel from the two consumers at build time.** Rejected: a generated package is harder to grep and harder to extend. Explicit Python wins.

## Verification

- `tests/test_architecture.py`: 7/7 passing.
- Existing test suite (`tests/`) passes without behavioral change after the imports were rewired.
