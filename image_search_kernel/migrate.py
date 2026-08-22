"""
image_search_kernel.migrate

Schema-version migration helper. Promotes a Qdrant collection from
one payload-schema version to another by copying each point and
applying a registered field transform per field.

Two strategies:
- `vector_strategy="copy"` (default) — vectors are copied unchanged.
  Used for schema-only migrations (e.g. v0 → v1 adding `folder` and
  `model_dim`).
- `vector_strategy="reembed"` — every source path is re-loaded and
  re-embedded by the provided `embedder`. Used for model migrations
  (e.g. SigLIP-2 gopt → L). Significantly more expensive.

The helper is a function (not a class). It runs synchronously, scroll
in batches. Long migrations should be invoked from a CLI / script,
not from a request handler.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from image_search_kernel.payload_schema import FIELD_SCHEMA_VERSION, SCHEMA_VERSION

__all__ = ["FieldTransform", "MigrationProgress", "MigrationReport", "migrate_collection"]


FieldTransform = Callable[["dict[str, object]", "ModelMeta"], object]
"""Callable that produces the value for one field of the new payload.

Receives the old payload and the model meta (registry-resolved).
Returns the value for the field this transform was registered
under. Per-field registration; one transform per declared field
in the target version.
"""


@dataclass(frozen=True)
class ModelMeta:
    name: str
    dim: int
    revision: str


@dataclass(frozen=True)
class MigrationProgress:
    phase: Literal["reading", "transforming", "writing", "done"]
    points_read: int
    points_written: int
    points_failed: int


@dataclass(frozen=True)
class MigrationReport:
    source_collection: str
    target_collection: str
    target_version: int
    vector_strategy: Literal["copy", "reembed"]
    total_read: int
    total_written: int
    total_failed: int
    failures: list[tuple[str, str]]  # (point_id, error_message)
    elapsed_seconds: float


@dataclass(frozen=True)
class _MigrationContext:
    target_version: int
    model_meta: ModelMeta
    field_transforms: dict[str, FieldTransform]


def migrate_collection(
    *,
    source: QdrantClient,
    target: QdrantClient,
    source_collection: str,
    target_collection: str,
    target_version: int | str,
    model_meta: ModelMeta,
    field_transforms: dict[str, FieldTransform],
    vector_strategy: Literal["copy", "reembed"] = "copy",
    batch_size: int = 256,
    on_progress: Callable[[MigrationProgress], None] | None = None,
) -> MigrationReport:
    """Promote `source_collection` to `target_collection` with `target_version`.

    Steps:
      1. Read every point from `source_collection` in batches.
      2. For each point: apply each registered field transform to the
         payload; set `_schema_version` to `target_version`.
      3. Copy the vector verbatim (or re-embed if requested — not
         implemented in v1).
      4. Upsert into `target_collection` (creating it on demand if
         not present).
      5. Return a `MigrationReport` with counts and per-failure detail.

    Failures are reported, not raised. The migration completes; the
    caller decides whether to retry or rollback. On any error during
    upsert, the affected point id is recorded in `failures` and the
    migration continues with the next batch.
    """
    import time

    t0 = time.perf_counter()

    # Resolve "v0" → 0, "v1" → 1, etc. Accept either int or "v<int>".
    if isinstance(target_version, str):
        target_version = int(target_version.lstrip("v"))

    # Refusal: target_version must be in the known-good set.
    if target_version != SCHEMA_VERSION:
        raise ValueError(
            f"target_version {target_version!r} is not in the known-good "
            f"set; the only supported version is {SCHEMA_VERSION}.",
        )

    ctx = _MigrationContext(
        target_version=target_version,
        model_meta=model_meta,
        field_transforms=field_transforms,
    )

    failures: list[tuple[str, str]] = []
    total_read = 0
    total_written = 0

    # Walk source collection by point id. Using scroll() is simpler than
    # offset pagination for migrations of unknown size.
    next_offset = None
    while True:
        try:
            points, next_offset = source.scroll(
                collection_name=source_collection,
                offset=next_offset,
                limit=batch_size,
                with_payload=True,
                with_vectors=(vector_strategy == "copy"),
            )
        except Exception as e:  # noqa: BLE001 — surface any Qdrant error as a recorded failure, not a crash
            failures.append((f"scroll@{next_offset}", f"scroll failed: {e}"))
            break
        if not points:
            break

        # Create target collection on demand. Idempotent if it exists.
        _ensure_target_collection(
            target, target_collection, dim=model_meta.dim,
        )

        for point in points:
            total_read += 1
            try:
                new_payload = _apply_transforms(ctx, point.payload or {})
                # Per the v0 → v1 contract: copy vectors unchanged.
                if vector_strategy != "copy":
                    raise NotImplementedError(
                        "vector_strategy='reembed' is not implemented in v1",
                    )
                target.upsert(
                    collection_name=target_collection,
                    points=[qmodels.PointStruct(
                        id=point.id,
                        vector=_as_dense_vector(point.vector),
                        payload=new_payload,
                    )],
                    wait=False,
                )
                total_written += 1
            except Exception as e:  # noqa: BLE001 — per-point isolation: skip the bad point, keep migrating
                failures.append((str(point.id), f"{type(e).__name__}: {e}"))

        if on_progress is not None:
            on_progress(MigrationProgress(
                phase="writing",
                points_read=total_read,
                points_written=total_written,
                points_failed=len(failures),
            ))

        if next_offset is None:
            break

    elapsed = time.perf_counter() - t0
    return MigrationReport(
        source_collection=source_collection,
        target_collection=target_collection,
        target_version=target_version,
        vector_strategy=vector_strategy,
        total_read=total_read,
        total_written=total_written,
        total_failed=len(failures),
        failures=failures,
        elapsed_seconds=elapsed,
    )


def _as_dense_vector(v: object) -> list[float]:
    """Coerce a Qdrant `point.vector` into the dense `list[float]` shape
    that `PointStruct.vector` accepts in the typed signature.

    Sparse vectors and multi-vector cases raise a typed error; the
    migration helper targets dense collections only.
    """
    if isinstance(v, list) and v and isinstance(v[0], (int, float)):
            return [float(x) for x in v]
    raise TypeError(
        f"migration helper only handles dense single-vector points; "
        f"got vector of type {type(v).__name__}",
    )


def _ensure_target_collection(
    client: QdrantClient, name: str, *, dim: int,
) -> None:
    """Create `name` if it doesn't exist. Idempotent."""
    existing = {c.name for c in client.get_collections().collections}
    if name in existing:
        return
    client.create_collection(
        collection_name=name,
        vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
    )


def _apply_transforms(
    ctx: _MigrationContext, old_payload: dict[str, object],
) -> dict[str, object]:
    """Apply each registered transform to `old_payload`, producing the
    new payload for the target version.
    """
    new_payload: dict[str, object] = {}
    for field_name, transform in ctx.field_transforms.items():
        new_payload[field_name] = transform(old_payload, ctx.model_meta)

    # Always set _schema_version on the migrated point.
    new_payload[FIELD_SCHEMA_VERSION] = ctx.target_version

    # Carry over any field from the old payload that wasn't transformed.
    # Tests verify no surprise deletions: anything not in the transform
    # map is preserved as-is.
    for k, v in old_payload.items():
        if k not in new_payload:
            new_payload[k] = v

    return new_payload


# --- Built-in transforms for the v0 → v1 schema migration -------------

def make_v0_to_v1_transforms() -> dict[str, FieldTransform]:
    """Return the field-transform map for v0 → v1 migration.

    The transforms here implement the §A2 migration contract:

    - `_schema_version`: set to 1 (handled by `_apply_transforms`, not
      here).
    - `folder`: derived from `path.parent`. Top-level files get
      `folder == <path>` (parent of a path with no parent is the
      path itself).
    - `model_dim`: copied from the registry-supplied ModelMeta.
    - Other fields: preserved as-is by the default carry-over rule.
    """
    return {
        "folder": _transform_folder,
        "model_dim": _transform_model_dim,
    }


def _transform_folder(
    old_payload: dict[str, object], model_meta: ModelMeta,
) -> str:
    """Derive `folder` from `old_payload['path']`."""
    path = old_payload.get("path", "")
    if not path:
        return ""
    # Use posix-style parent. The path is absolute source-path from
    # the indexer; on Windows the indexer produces posix-style too.
    from pathlib import PurePosixPath

    return str(PurePosixPath(str(path)).parent)


def _transform_model_dim(
    old_payload: dict[str, object], model_meta: ModelMeta,
) -> int:
    """Return the model_meta dim (registry-supplied)."""
    return model_meta.dim
