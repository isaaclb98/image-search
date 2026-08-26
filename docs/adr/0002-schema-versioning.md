# ADR-0002: Schema versioning on every Qdrant point

**Status:** Accepted.
**Date:** 2026-08.
**Related:** `docs/archive/backend-refactor-plan.md` (archived) §4.2, §4.11, §4.2.1.

## Context

Before this refactor, the Qdrant payload had no version field. The schema was implicit: every writer and reader agreed on field names by convention. Any future change — a rename, a value-format change, a model-swap — required either:

1. A wipe-and-reindex of every collection, OR
2. A silent misinterpretation if old and new fields overlapped.

The desktop product (§7 deferrals) needs to coexist with the web backend in the same Qdrant instance if a deployment chooses that topology. Without versioning, that's impossible without collisions.

## Decision

Every point carries a `_schema_version` payload field. The current version is `SCHEMA_VERSION = 1`, declared in `image_search_kernel/payload_schema.py`. Every writer sets it (`indexer/upsert.py:build_payload`); every reader checks it and refuses unknown versions with a typed error.

The version-1 schema adds three fields over the legacy v0:

- `_schema_version: int = 1` — required.
- `folder: str` — absolute parent directory path of the source image. Powers folder-browsing in the desktop product and folder-grouped hydration in the search-side cache.
- `model_dim: int` — vector dimension produced by the model. Self-describing, so a backfilled migration can verify each point's vector length matches its recorded dim without consulting the model registry.

`_schema_version` and `model_revision` are independent axes: a point can keep `_schema_version=1` while `model_revision` changes (model upgrade), or vice versa. The doc spells this out so a future reader doesn't conflate them.

A migration helper at `image_search_kernel/migrate.migrate_collection` promotes a collection from one version to another by copying vectors and applying per-field transforms registered by the caller. Vector strategy defaults to `copy` (schema-only migration). The `reembed` strategy is declared but not implemented in v1 — it's a separate operational concern.

Refusal semantics: a search request against a collection whose `_schema_version` is not in the known-good set returns HTTP 503 with a stable JSON body:

```json
{"error": "schema_version_mismatch", "found": <int>, "supported": [<int>, ...], "collection": "<name>"}
```

Logged at ERROR with the same fields. No silent interpretation of an unknown payload shape.

## Consequences

- **Positive:** Future field renames or value-format changes become backfillable rather than wipe-and-reindex.
- **Positive:** Two products (web + desktop) with different models can share a Qdrant instance with different schema versions on different collections, if a deployment chooses that.
- **Positive:** `folder` enables folder-browsing without a separate scan at request time.
- **Negative:** Every point now carries an extra integer in its payload. At 50K points × 4 bytes ≈ 200KB total — negligible.
- **Negative:** Readers must check the field. Failure mode (silent accept) is enforced by `tests/test_schema.py:test_schema_version_is_positive_int` and the architectural rule that `parse_payload` raises a typed error on unknown versions.
- **Migration:** Existing collections are un-versioned (`v0` implicit). The migration helper is the operator-facing tool to bring them to v1. Not auto-run; the refusal semantics make the choice explicit at the operator level.

## Alternatives considered

- **No versioning, just rename fields when needed.** Rejected: every consumer that hard-coded field names would break simultaneously.
- **Implicit versioning via Qdrant collection naming.** Rejected: collection names are not a version axis; one collection can hold points with multiple versions over its lifetime.
- **Version per field, not per point.** Rejected: a per-field version complicates read paths and the migration helper. Per-point is simpler and the schema is small enough that adding a new field is rare.

## Verification

- `tests/test_schema.py`: 11/11 passing (version, folder, model_dim, require_fields).
- `tests/test_migrate.py`: 6/6 passing (end-to-end migration with real Qdrant in-memory).
- `tests/test_architecture.py:test_search_does_not_import_indexer` and `test_indexer_does_not_import_search` confirm the post-refactor dep direction.
