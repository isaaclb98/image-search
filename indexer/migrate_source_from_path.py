"""
indexer/migrate_source_from_path.py — one-shot migration.

Derives `source` from the file path on every `images` point and
writes it back. Idempotent: skips points whose `source` already
matches the computed value.

Logic:
  - Read payload.path (UNC form, e.g.
    ``\\\\192.168.250.108\\files\\images\\kpop\\collections\\foo.jpg``).
  - Normalize separators to "/", then look at the segment pair
    after `/images/`. The first two segments are the source label,
    with backslashes converted to forward slashes:
      .../images/kpop/collections/...  -> source = "kpop/collections"
      .../images/kpop/data/...          -> source = "kpop/data"
  - Set source = computed value if it differs from the current one.
  - Skip points whose path doesn't match any known source; they
    are counted as `unmapped` so the operator can extend the map.

Run on the cluster against the in-cluster Qdrant:

    python -m indexer.migrate_source_from_path \
        --qdrant-url http://qdrant.qdrant.svc.cluster.local:6333 \
        [--batch-size 1000] [--dry-run]

The migration is bounded by the points_count of the target
collection. Each batch is one scroll + N small set_payload calls
(one per distinct computed source within the batch).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from typing import Any

from dotenv import load_dotenv
from qdrant_client import QdrantClient

from image_search_kernel.qdrant_url import client_kwargs as _qdrant_client_kwargs
from indexer import upsert

load_dotenv()

logger = logging.getLogger(__name__)

# Source values are the two segment pair immediately after
# `/images/` in the canonical path. The set is closed today; any
# point whose path doesn't match is reported as `unmapped` and not
# touched. Extend with a `collections.data`-style mapping if more
# top-level groups are added.
_SOURCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"/images/([^/]+/[^/]+)/", re.IGNORECASE),
)


def _compute_source(path: str) -> str | None:
    """Return the source label for a canonical path, or None if unmapped.

    The canonical payload path is in UNC form with backslashes
    (`\\192.168.250.108\\files\\images\\kpop\\collections\\...`).
    Normalize by flipping `\\` to `/` before matching.
    """
    if not path:
        return None
    normalized = path.replace("\\", "/")
    for pat in _SOURCE_PATTERNS:
        m = pat.search(normalized)
        if m:
            return m.group(1)
    return None


def _make_client(args: argparse.Namespace) -> QdrantClient:
    if args.qdrant_in_memory:
        return QdrantClient(location=":memory:")
    return QdrantClient(**_qdrant_client_kwargs(
        url=args.qdrant_url,
        api_key=args.qdrant_api_key,
        timeout=60,
    ))


def migrate(
    client: QdrantClient,
    collection: str = upsert.DEFAULT_COLLECTION,
    batch_size: int = 1000,
    dry_run: bool = False,
    quiet: bool = False,
) -> dict:
    info = client.get_collection(collection)
    total = int(getattr(info, "points_count", 0) or 0)
    if not quiet:
        print(f"migrate-source: collection={collection!r} points={total} "
              f"batch_size={batch_size} dry_run={dry_run}", flush=True)

    updated = already_correct = unmapped = no_path = errors = 0
    offset: str | int | None = None
    started = time.time()
    # Map of current_source -> count, for the dry-run/preview report.
    drift: dict[str, dict[str, int]] = {}
    while True:
        try:
            batch, offset = client.scroll(
                collection_name=collection,
                limit=batch_size,
                offset=offset,
                with_payload=["path", "source"],
                with_vectors=False,
            )
        except Exception as e:
            logger.exception("scroll failed: %s", e)  # noqa: TRY401
            errors += 1
            break
        if not batch:
            break
        # Group pending updates by desired source so each set_payload
        # carries a single uniform payload — one round trip per
        # distinct desired source value, not per point.
        # `updates[desired_source] = {current_or_new: [ids...]}`
        # To keep behavior simple and minimize round trips, we do
        # one set_payload per (desired_source, ids) group;
        # qdrant_client.set_payload treats the whole `points` arg
        # in a single request. So we just bucket by desired source.
        updates: dict[str, list[Any]] = {}
        for p in batch:
            payload = p.payload or {}
            path = payload.get("path")
            if not path:
                no_path += 1
                continue
            desired = _compute_source(path)
            if desired is None:
                unmapped += 1
                continue
            current = payload.get("source")
            if current == desired:
                already_correct += 1
                continue
            if not dry_run:
                updates.setdefault(desired, []).append(p.id)
            else:
                # Track drift for the preview report.
                bucket = drift.setdefault(desired, {})
                bucket[current or "<unset>"] = bucket.get(current or "<unset>", 0) + 1
        if updates and not dry_run:
            for value, ids in updates.items():
                client.set_payload(
                    collection_name=collection,
                    points=ids,
                    payload={"source": value},
                )
                updated += len(ids)
        elif updates:
            for value, ids in updates.items():
                bucket = drift.setdefault(value, {})
                # In dry-run, we already recorded by current value in
                # the main loop; just count.
                bucket["<total>"] = bucket.get("<total>", 0) + len(ids)
        if not quiet and (already_correct + updated + unmapped) % 10000 < batch_size:
            print(f"  ... updated={updated} already_correct={already_correct} "
                  f"unmapped={unmapped} no_path={no_path}", flush=True)
        if offset is None:
            break

    elapsed = time.time() - started
    stats = {
        "total_points": total,
        "updated": updated,
        "already_correct": already_correct,
        "unmapped": unmapped,
        "no_path": no_path,
        "errors": errors,
        "elapsed_seconds": round(elapsed, 2),
    }
    if dry_run:
        stats["drift_preview"] = drift
    if not quiet:
        print(f"migrate-source: done {stats}", flush=True)
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="migrate_source_from_path",
        description="One-shot migration: derive `source` from the file "
                    "path on each `images` point. Idempotent.",
    )
    p.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    p.add_argument("--qdrant-api-key", default=os.environ.get("QDRANT_API_KEY") or None)
    p.add_argument("--qdrant-in-memory", action="store_true",
                   help="use in-memory Qdrant (tests)")
    p.add_argument("--qdrant-collection", default=upsert.DEFAULT_COLLECTION,
                   help="Qdrant collection to migrate (default: images)")
    p.add_argument("--batch-size", type=int, default=1000)
    p.add_argument("--dry-run", action="store_true",
                   help="scroll + count, don't write")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = parse_args(argv)
    client = _make_client(args)
    try:
        migrate(
            client,
            collection=args.qdrant_collection,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            quiet=args.quiet,
        )
    except Exception as e:
        print(f"migrate-source failed: {e}", file=sys.stderr)
        logger.exception("migrate-source failed")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())