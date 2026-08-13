"""
indexer/upsert.py

Qdrant writes. Idempotent by design.

The id strategy is: `sha1(f"{shard}::{path.as_posix()}").hexdigest()[:32]`.
This means:
  - Re-running the indexer on the same folder produces the same ids
    (Qdrant skips on duplicate).
  - Different shards can hold the same path (different ids).
  - The 32-char prefix is plenty for collision-resistance over
    realistic collection sizes (< 1B points).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from indexer.blurhash import compute_blurhash
from indexer.fingerprints import compute_fingerprints

logger = logging.getLogger(__name__)

# SigLIP2 gopt-16 output dim. Hard-coded — change only if the model changes.
VECTOR_DIM: int = 1536

# Qdrant collection name. Single collection, hard-coded in v1.
DEFAULT_COLLECTION: str = "images"

# Fixed namespace for uuid5 — anything works as long as it's stable.
# Random UUID generated once and pinned for the project.
_ID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # standard NAMESPACE_URL


def id_for(path: Path, shard: str = "") -> str:
    """
    Deterministic Qdrant point id for a given (path, shard).

    Uses uuid5 (sha1-based) for stable, collision-resistant ids that
    Qdrant accepts as native UUIDs in both server and in-memory mode.
    The 36-char string form is what's stored in the point id and
    returned in search results.

    Args:
        path: the source image path
        shard: optional shard label (lets multiple sub-corpora share
            a collection)

    Returns:
        UUID-format string (36 chars, with hyphens)
    """
    key = f"{shard}::{path.as_posix()}"
    return str(uuid.uuid5(_ID_NAMESPACE, key))


def build_payload(
    path: Path, shard: str, model_name: str, model_revision: str,
    collection: str = "",
) -> dict:
    """
    Build the Qdrant payload for a given image path.

    The payload is intentionally minimal in v1 — just enough to
    look up a result and render a detail page. Add fields as
    concrete use cases demand.

    Args:
        path: absolute source path of the image
        shard: optional shard label
        model_name: the embedding model name
        model_revision: the embedding model revision
        collection: the logical library this photo belongs to
            (kpop, portrait, general, ...). Required at index time
            and indexed as a Qdrant keyword payload field so the
            search side can filter with a native MatchAny query.
    """
    stat = path.stat()
    blurhash = compute_blurhash(path)
    fingerprints = compute_fingerprints(path)
    return {
        "id": id_for(path, shard),
        "path": str(path.resolve()),
        "shard": shard,
        "collection": collection,
        # T9 — LQIP. None when compute failed (file missing / unreadable /
        # non-image); the client skips the placeholder render in that case.
        "blurhash": blurhash,
        # Search Diversity metadata. These are intentionally not indexed
        # as Qdrant payload fields; the ranker reads them from candidates
        # after the vector search.
        **fingerprints,
        "mtime": int(stat.st_mtime),
        "size": int(stat.st_size),
        "model_name": model_name,
        "model_revision": model_revision,
        "indexed_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
    }


def ensure_payload_index(
    client: QdrantClient, name: str, field: str, field_type: str = "keyword"
) -> None:
    """
    Create a payload index on `field` if one doesn't already exist.

    The indexer calls this once per run, on every collection it
    touches, so re-runs are idempotent. The search side uses a
    `MatchAny` filter on this field; without a payload index that
    filter is a full scan, which is what we want to avoid.
    """
    try:
        client.create_payload_index(
            collection_name=name,
            field_name=field,
            field_type=field_type,
        )
        logger.info("created payload index on %s.%s (%s)", name, field, field_type)
    except Exception as e:
        # Qdrant returns an error if the index already exists. That's
        # fine — we only want to ensure it's there.
        msg = str(e).lower()
        if "already exists" in msg or "exists" in msg:
            return
        raise


def ensure_collection(
    client: QdrantClient, name: str = DEFAULT_COLLECTION, dim: int = VECTOR_DIM
) -> None:
    """
    Create the collection if it doesn't exist. Idempotent.

    Vector config:
      - 1536-dim
      - Cosine distance (SigLIP2 is trained for cosine similarity)
    """
    existing = {c.name for c in client.get_collections().collections}
    if name in existing:
        logger.debug("collection %r already exists", name)
        return

    logger.info("creating collection %r (dim=%d, distance=Cosine)", name, dim)
    client.create_collection(
        collection_name=name,
        vectors_config=qmodels.VectorParams(
            size=dim,
            distance=qmodels.Distance.COSINE,
        ),
    )


def upsert_batch(
    client: QdrantClient,
    name: str,
    items: Sequence[tuple[str, list[float], dict]],
    wait: bool = False,
) -> None:
    """
    Upsert a batch of (id, vector, payload) tuples.

    Args:
        client: Qdrant client
        name: collection name
        items: sequence of (id, vector, payload)
        wait: if True, block until the upsert is indexed. The indexer
            uses wait=False during the run and wait=True on the
            final batch to ensure consistent state.
    """
    if not items:
        return
    points = [
        qmodels.PointStruct(id=pid, vector=vec, payload=payload)
        for (pid, vec, payload) in items
    ]
    client.upsert(collection_name=name, points=points, wait=wait)


def existing_ids(
    client: QdrantClient, name: str, ids: Iterable[str]
) -> set[str]:
    """
    Return the subset of `ids` that already exist in the collection.

    Used by the indexer to skip already-indexed files (idempotency).
    Qdrant doesn't have a "point exists?" call, so we retrieve by ids
    and check what comes back non-null.
    """
    ids = list(ids)
    if not ids:
        return set()

    found: set[str] = set()
    # Qdrant's `retrieve` returns points in the same order as the input.
    points = client.retrieve(collection_name=name, ids=ids, with_payload=False)
    for p in points:
        found.add(str(p.id))
    return found


def prune_missing(
    client: QdrantClient,
    name: str,
    source_dirs: list[str] | None = None,
    batch_size: int = 1000,
    prefix: str = "",
    base: str = "",
) -> int:
    """
    Scroll all points in the collection and delete the ones whose
    stored path doesn't exist on disk.

    When `source_dirs` is provided, the liveness check is a set
    membership test against a single filesystem walk over those dirs
    (much faster at 1.5M+ scale than per-point `Path.exists()`).
    When `source_dirs` is None, the slower per-point check is used —
    fine for small collections but hours on a slow share at scale.

    `prefix` / `base` make the walk canonical-aware, matching the
    translation local_sync applies at upsert time. Stored payload
    paths are canonical UNC (`\\192.168.250.108\files\images\...`)
    while the filesystem walk produces local paths
    (`Z:/images/kpop/...`); a raw string membership test between the
    two would classify every point as dead and delete the whole
    collection. With `base`+`prefix` set, each walked file is
    translated to its canonical form before the check, so live points
    survive and only genuinely-missing files get pruned.

    Returns the number of points deleted.
    """
    removed = 0

    # Pre-walk: build a set of every existing path under the source
    # dirs. One filesystem walk is dramatically faster than 1.5M
    # individual stat() calls (which is what the no-arg path used to
    # do). The walk is opt-in: callers that don't know their source
    # dirs fall back to the per-point check.
    existing_paths: set[str] | None = None
    if source_dirs:
        existing_paths = set()
        base_path = Path(base).resolve() if (prefix and base) else None
        for src in source_dirs:
            src_path = Path(src)
            if not src_path.exists() or not src_path.is_dir():
                logger.warning(
                    "prune: source dir does not exist or is not a directory: %s", src
                )
                continue
            for p in src_path.rglob("*"):
                if p.is_file():
                    lp = p.resolve()
                    if base_path is not None:
                        try:
                            rel = lp.relative_to(base_path)
                            existing_paths.add(str(Path(prefix) / rel))
                            continue
                        except ValueError:
                            pass  # outside base — fall through to raw
                    existing_paths.add(str(lp))
        logger.info(
            "prune: pre-walked %d files under %d source dir(s) "
            "(prefix=%r base=%r)",
            len(existing_paths), len(source_dirs), prefix, base,
        )

    def _is_alive(path_str: str) -> bool:
        if not path_str:
            return False  # no path = orphan
        if existing_paths is not None:
            return path_str in existing_paths
        try:
            return Path(path_str).exists()
        except OSError:
            return False

    next_offset: int | None = None
    while True:
        points = client.scroll(
            collection_name=name,
            limit=batch_size,
            offset=next_offset,
            with_payload=True,
            with_vectors=False,
        )
        batch, next_offset = points
        if not batch:
            break

        to_delete: list[str | int] = []
        for p in batch:
            path_str = (p.payload or {}).get("path", "")
            if not _is_alive(path_str):
                to_delete.append(str(p.id))

        if to_delete:
            client.delete(
                collection_name=name,
                points_selector=to_delete,
                wait=True,
            )
            removed += len(to_delete)
            logger.info("prune: removed %d points (batch)", len(to_delete))

        if next_offset is None:
            break

    return removed
