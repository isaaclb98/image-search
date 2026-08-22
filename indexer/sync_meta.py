"""
indexer/sync_meta.py — Tiny shared module for sync state.

When `indexer/sync.py` was removed (the old k8s scanner CLI), the
shared constants and helpers it owned (META_COLLECTION, META_POINT_ID,
ensure_sync_collections, write_meta) still had callers outside the
deleted file — specifically:

  * search/index_db.py reads META_COLLECTION / META_POINT_ID to
    publish drift-detection markers into the same `_sync_meta` point
    the scanner used to write.
  * tests/test_index_db_drift.py calls ensure_sync_collections +
    write_meta to seed Qdrant fixtures.

This module keeps just those primitives so the rest of the codebase
doesn't have to grow imports into a now-defunct CLI entry point.
The actual scan/embed/orphan logic lives in indexer/local_sync.py
(Windows path) — there's no longer a CLI in this folder.

Naming: `sync_meta` is the Qdrant collection name (`_sync_meta`); the
module name matches the resource, not a workflow.
"""

from __future__ import annotations

from datetime import datetime, timezone

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from image_search_kernel.registry import get as _registry_get

# Qdrant collection names. Single source of truth so search/db code
# and any future sync code agree on what the marker point is called.
META_COLLECTION = "_sync_meta"
# Singleton point id for `_sync_meta`. Must be a valid UUID because
# the in-memory Qdrant backend rejects non-UUID string ids. Stable
# across processes so consumers (search/db) can read it without
# coordinating write order.
META_POINT_ID = "11111111-1111-1111-1111-111111111111"
PENDING_COLLECTION = "_pending"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_sync_collections(client: QdrantClient, images_collection: str = "images") -> None:
    """
    Create the `images`, `_pending`, and `_sync_meta` collections if missing.

    Idempotent. The `images` collection gets the real SigLIP2 dim
    (VECTOR_DIM = 1536) so it's ready for vector upserts; the two
    auxiliary collections (`_pending`, `_sync_meta`) carry scalar
    payloads only, so they get a 1-dim placeholder.

    Callers that need a specific dim for the images collection can
    pre-create it via `upsert.ensure_collection(client, name, dim)`
    before calling this; if it already exists at the requested dim,
    the `get_collection` check below short-circuits and leaves it
    alone (we never re-configure an existing collection).
    """
    targets = [
        (images_collection, _registry_get("ViT-gopt-16-SigLIP2-384").dim),
        (PENDING_COLLECTION, 1),
        (META_COLLECTION, 1),
    ]
    for name, dim in targets:
        try:
            client.get_collection(collection_name=name)
        except Exception:
            client.create_collection(
                collection_name=name,
                vectors_config=qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE),
            )


def write_meta(client: QdrantClient, payload: dict) -> None:
    """
    Write the singleton `_sync_meta` point with the given payload.

    Used by the scan/embed pipeline to record the last successful
    run (when, what, how many points touched). The search side reads
    this to detect drift between Qdrant state and on-disk state.
    """
    ensure_sync_collections(client)
    client.upsert(
        collection_name=META_COLLECTION,
        points=[
            qmodels.PointStruct(
                id=META_POINT_ID,
                vector=[0.0],
                payload={**payload, "updated_at": _utc_now()},
            )
        ],
        wait=True,
    )


def pending_count(client: QdrantClient) -> int:
    """Return the number of points currently sitting in `_pending`.

    Kept here for any test fixture that wants to assert the queue is
    empty/drained. Production no longer writes to `_pending` — use
    `local_sync.py` for that.
    """
    try:
        result = client.count(collection_name=PENDING_COLLECTION)
        return int(result.count)
    except Exception:
        return 0
