#!/usr/bin/env python3
"""
indexer/indexer.py — CLI entry point.

Walks a folder of images, embeds each with SigLIP2, upserts to Qdrant.
Idempotent: re-running on the same folder is a no-op (skips already-indexed).

Usage:
    python -m indexer.indexer <SOURCE_DIR> --collection NAME [options]

Options:
    --batch-size N    Embedding batch size (default 16; saturates 24GB GPU)
    --shard LABEL     Optional shard label stored in payload
    --limit N         Stop after N files (0 = no limit; useful for smoke tests)
    --dry-run         Walk + embed; do not upsert. Prints counts.
    --model NAME      open_clip arch tag (default ViT-gopt-16-SigLIP2-384)
    --device DEV      cuda or cpu (default: cuda)
    --qdrant-url URL  Qdrant endpoint (default http://localhost:6333)
    --qdrant-api-key KEY  Qdrant API key (default: $QDRANT_API_KEY or unset)
    --collection NAME  Logical library name (kpop, portrait, general, ...).
                       Required. Stored in payload and indexed for fast filter.
                       Qdrant collection name itself is fixed (default images).
    --qdrant-collection NAME  Qdrant collection name (default images)
    --qdrant-in-memory  Use in-memory Qdrant (for tests; overrides --qdrant-url)
    --prune           Remove points whose source file no longer exists on disk
    --cache-file PATH  Path to the indexer cache file (default state/indexer_cache.json).
                       Tracks "what's already indexed" so re-runs don't need to
                       ask Qdrant per file.
    --no-cache        Skip the local cache; ask Qdrant per batch (slow but
                       always fresh). Use this for one-off runs when you don't
                       want the cache to mask out-of-band changes.
    --refresh-cache   Discard the local cache and rebuild from a Qdrant scroll
                       on startup. Use after manual point deletions, restores,
                       or anything that desyncs the cache from reality.

    --reblurhash       Walk the existing collection, recompute blurhash per point
                       from its source file, and rewrite only the 'blurhash' payload
                       field. Does NOT re-embed. Idempotent.

Exit codes:
    0  success
    1  unhandled error
    2  invalid arguments
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient

from indexer import scan, upsert
from indexer.cache import DEFAULT_CACHE_PATH, IndexerCache
from indexer.image_loader import LoaderError, letterbox_resize, load
from indexer.vision_encoder import VisionEncoder
from search.qdrant_url import client_kwargs as _qdrant_client_kwargs

# Load .env from the cwd (or any ancestor) on startup. Values present in
# the real process env take precedence — real env wins over .env.
load_dotenv()

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="indexer",
        description="Embed images with SigLIP2 and upsert to Qdrant.",
    )
    p.add_argument("source", type=Path, help="folder to scan")
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--shard", type=str, default="")
    p.add_argument("--limit", type=int, default=0, help="0 = no limit")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--model", type=str, default="ViT-gopt-16-SigLIP2-384")
    p.add_argument("--device", type=str, default="cuda")
    p.add_argument("--qdrant-url", type=str, default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    p.add_argument("--qdrant-api-key", type=str, default=os.environ.get("QDRANT_API_KEY") or None,
                   help="Qdrant API key (default: $QDRANT_API_KEY)")
    p.add_argument(
        "--collection",
        type=str,
        required=True,
        help="Logical library name (kpop, portrait, general, ...). "
             "Stored in the payload and indexed for fast search-side filtering.",
    )
    p.add_argument(
        "--qdrant-collection",
        type=str,
        default="images",
        help="Qdrant collection name (default: images). All logical "
             "libraries share one collection; --collection tags each "
             "point with its library.",
    )
    p.add_argument(
        "--qdrant-in-memory",
        action="store_true",
        help="use in-memory Qdrant (overrides --qdrant-url)",
    )
    p.add_argument(
        "--prune",
        action="store_true",
        help="scan collection and delete points whose source file no longer exists on disk",
    )
    p.add_argument(
        "--cache-file",
        type=Path,
        default=DEFAULT_CACHE_PATH,
        help="path to the indexer cache file (default: state/indexer_cache.json)",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="skip the local cache; ask Qdrant per batch (slow but always fresh)",
    )
    p.add_argument(
        "--refresh-cache",
        action="store_true",
        help="discard the local cache and rebuild from Qdrant on startup",
    )
    p.add_argument(
        "--reblurhash",
        action="store_true",
        help="walk the existing collection, recompute blurhash for each point from its source "
             "file, and rewrite only the 'blurhash' payload field via set_payload. Does NOT "
             "re-embed. Idempotent: re-running on a current collection is a no-op. Mutually "
             "exclusive with the normal index path.",
    )
    p.add_argument(
        "--refingerprint",
        action="store_true",
        help="walk the existing collection, recompute content_sha256 and dhash from each "
             "source file, and rewrite only those payload fields. Does NOT re-embed. "
             "Idempotent and mutually exclusive with the normal index path.",
    )
    return p.parse_args(argv)


def iter_batches(items: list, size: int) -> Iterator[list]:
    """Yield successive `size`-sized chunks of `items`."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


def make_qdrant_client(args: argparse.Namespace) -> QdrantClient:
    if args.qdrant_in_memory:
        return QdrantClient(location=":memory:")
    return QdrantClient(**_qdrant_client_kwargs(
        url=args.qdrant_url,
        api_key=args.qdrant_api_key,
        timeout=30,  # int seconds; qdrant-client 1.18 rejects float
    ))


def main(argv: list[str] | argparse.Namespace | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = argv if isinstance(argv, argparse.Namespace) else parse_args(argv)

    if not args.source.exists():
        logger.error("source does not exist: %s", args.source)
        return 2
    if not args.source.is_dir():
        logger.error("source is not a directory: %s", args.source)
        return 2

    client = make_qdrant_client(args)

    if args.reblurhash and args.refingerprint:
        logger.error("--reblurhash and --refingerprint are mutually exclusive")
        return 2

    # Ensure the collection (and its payload index) exist BEFORE
    # the cache init, so the cache's `rebuild_from_qdrant` has
    # something to scroll. `ensure_collection` is idempotent, so
    # this is a no-op on re-runs.
    if not args.dry_run:
        upsert.ensure_collection(client, args.qdrant_collection)
        # Idempotent: creates a keyword index on the `collection` field
        # if it doesn't already exist. The search side filters on this
        # field with MatchAny; without the index, that's a full scan.
        upsert.ensure_payload_index(
            client, args.qdrant_collection, "collection", "keyword",
        )

    # ---- Cache: local fast-path for "is this already indexed?" ----
    # Default-on. Falls back to per-batch Qdrant retrieve calls if
    # disabled, can't be loaded, or can't be rebuilt. The cache is
    # keyed by (path, mtime, size) so a file modification invalidates
    # its entry automatically.
    use_cache = False
    cache: IndexerCache | None = None
    if not args.no_cache and not args.dry_run:
        cache = IndexerCache(args.cache_file, args.qdrant_collection)
        if args.refresh_cache or not cache.load():
            if args.refresh_cache:
                logger.info("cache: --refresh-cache, rebuilding from Qdrant")
            else:
                logger.info("cache: no existing cache, building from Qdrant")
            try:
                cache.rebuild_from_qdrant(client, args.qdrant_collection)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "cache: rebuild from Qdrant failed: %s; falling back to per-batch checks",
                    e,
                )
                cache = None
            else:
                try:
                    cache.save()
                except Exception as e:  # noqa: BLE001
                    logger.warning("cache: initial save failed: %s", e)
                use_cache = True
        else:
            use_cache = True
        if use_cache and cache is not None:
            logger.info("cache: ready, %d entries", len(cache))

    if args.prune:
        logger.info("pruning missing files from collection %s", args.qdrant_collection)
        # Pass the source dir so the prune uses a filesystem-walk +
        # set-membership check (much faster than per-point stat()).
        # For multi-source collections, callers would loop over each
        # source dir; the v1 supports one --source per prune run.
        removed = upsert.prune_missing(
            client, args.qdrant_collection,
            source_dirs=[args.source] if args.source else None,
        )
        dropped = cache.remove_missing() if cache is not None else 0
        if dropped and cache is not None:
            try:
                cache.save()
            except Exception as e:  # noqa: BLE001
                logger.warning("cache: prune save failed: %s", e)
        logger.info(
            "prune complete: removed %d points, %d cache entries", removed, dropped,
        )
        return 0

    if args.reblurhash:
        # Walk the collection with cursor-paginated scrolls. We don't
        # embed (the vector stays put) — only the `blurhash` payload
        # field is rewritten. Idempotent: re-running on an already-
        # complete collection is a no-op (skipped counter ticks up).
        from indexer.blurhash import compute_blurhash as _compute_blurhash
        logger.info("reblurhash: walking collection %s", args.qdrant_collection)
        updated = skipped = failed = 0
        offset = None
        while True:
            points, next_offset = client.scroll(
                collection_name=args.qdrant_collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break
            for rec in points:
                payload = rec.payload or {}
                path_str = payload.get("path")
                if not path_str:
                    failed += 1
                    continue
                new_hash = _compute_blurhash(Path(path_str))
                if new_hash is None:
                    failed += 1
                    continue
                if payload.get("blurhash") == new_hash:
                    skipped += 1
                    continue
                client.set_payload(
                    collection_name=args.qdrant_collection,
                    payload={"blurhash": new_hash},
                    points=[rec.id],
                )
                updated += 1
            if next_offset is None:
                break
            offset = next_offset
        logger.info(
            "reblurhash complete: updated=%d skipped=%d failed=%d",
            updated, skipped, failed,
        )
        return 0

    if args.refingerprint:
        from indexer.fingerprints import compute_fingerprints as _compute_fingerprints

        logger.info("refingerprint: walking collection %s", args.qdrant_collection)
        updated = skipped = failed = 0
        offset = None
        while True:
            points, next_offset = client.scroll(
                collection_name=args.qdrant_collection,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break
            for rec in points:
                payload = rec.payload or {}
                path_str = payload.get("path")
                if not path_str:
                    failed += 1
                    continue
                fingerprints = _compute_fingerprints(Path(path_str))
                if not fingerprints["content_sha256"]:
                    failed += 1
                    continue
                if all(payload.get(key) == value for key, value in fingerprints.items()):
                    skipped += 1
                    continue
                client.set_payload(
                    collection_name=args.qdrant_collection,
                    payload=fingerprints,
                    points=[rec.id],
                )
                updated += 1
            if next_offset is None:
                break
            offset = next_offset
        logger.info(
            "refingerprint complete: updated=%d skipped=%d failed=%d",
            updated, skipped, failed,
        )
        return 0

    t0 = time.time()
    logger.info("scanning %s (collection=%s)", args.source, args.collection)
    paths = scan.snapshot(args.source)
    if args.limit:
        paths = paths[: args.limit]
    logger.info("found %d image files in %s", len(paths), args.source)

    if not paths:
        print("Done. Indexed: 0, Skipped: 0, Errors: 0")
        return 0

    encoder = VisionEncoder(
        arch=args.model, device=args.device
    )

    indexed = 0
    skipped = 0
    errors = 0
    is_last_batch = False

    batches = list(iter_batches(paths, args.batch_size))
    for batch_idx, batch in enumerate(batches):
        is_last_batch = batch_idx == len(batches) - 1

        # Step 1: filter by cache FIRST, then load only the misses.
        # The old order (load → check cache) wasted the I/O of opening
        # 60k images for a no-change re-index. The cache check is one
        # stat() per file (~10-20μs local) vs the I/O of a JPEG read
        # (can be 10-100× slower, and on a stalled share: forever).
        # We also time the load and WARN on slow files so you can see
        # when a particular share/folder is the problem without
        # waiting for the timeout to fire.
        new_loaded: list[tuple[Path, object]] = []  # (path, PIL.Image)
        batch_skipped = 0
        for p in batch:
            if use_cache and cache is not None and cache.has(p):
                batch_skipped += 1
                continue
            t_load = time.time()
            try:
                img = load(p)
            except LoaderError as e:
                logger.warning("loader error: %s (%s)", p, e)
                errors += 1
                continue
            elapsed = time.time() - t_load
            if elapsed > 1.0:
                # The fact that this fires is useful diagnostic info
                # for slow shares / large images / antivirus scans.
                # The path is in the message so you can identify the
                # specific file (or pattern of files) that's slow.
                logger.warning(
                    "slow load: %s took %.2fs", p, elapsed,
                )
            new_loaded.append((p, img))
        skipped += batch_skipped

        if not new_loaded:
            continue

        # Step 2: legacy per-batch Qdrant retrieve path (only when the
        # cache is disabled and we're not in dry-run). In the cache
        # path the filter already happened in step 1.
        if not (use_cache and cache is not None) and not args.dry_run:
            ids_to_check = [upsert.id_for(p, args.shard) for p, _ in new_loaded]
            already = upsert.existing_ids(
                client, args.qdrant_collection, ids_to_check
            )
            new_loaded = [
                (p, img) for (p, img), pid in zip(new_loaded, ids_to_check, strict=False) if pid not in already
            ]
            if not new_loaded:
                continue

        # Step 3: embed (letterbox first to match isaac-image-scoring exactly)
        try:
            vectors = encoder.embed_batch(
                [letterbox_resize(img) for _, img in new_loaded]
            )
        except Exception:
            logger.exception("embed failed for batch starting at %s", new_loaded[0][0])
            errors += len(new_loaded)
            continue

        # Step 4: upsert
        items = [
            (
                upsert.id_for(p, args.shard),
                vec,
                upsert.build_payload(
                    p, args.shard, args.model, "", args.collection,
                ),
            )
            for (p, _), vec in zip(new_loaded, vectors, strict=False)
        ]
        if not args.dry_run:
            try:
                upsert.upsert_batch(
                    client, args.qdrant_collection, items, wait=is_last_batch
                )
            except Exception:
                logger.exception("upsert failed for batch starting at %s", new_loaded[0][0])
                errors += len(new_loaded)
                continue
        indexed += len(new_loaded)

        # Step 5: update the cache with what we just indexed. Save
        # per batch (not per upsert) — at most 16 entries are lost
        # on a crash, vs 16 file writes × 1ms = trivial I/O.
        if use_cache and cache is not None and not args.dry_run:
            for (p, _), _vec in zip(new_loaded, vectors, strict=False):
                try:
                    stat = p.stat()
                except OSError:
                    continue
                cache.add(
                    p,
                    upsert.id_for(p, args.shard),
                    int(stat.st_mtime),
                    int(stat.st_size),
                )
            try:
                cache.save()
            except Exception as e:  # noqa: BLE001
                logger.warning("cache: save failed (will rebuild next run): %s", e)

        logger.info(
            "batch %d/%d: indexed %d, skipped %d (running total: %d/%d, errors=%d)",
            batch_idx + 1,
            len(batches),
            len(new_loaded),
            batch_skipped,
            indexed,
            len(paths),
            errors,
        )

    dt = time.time() - t0
    print(f"Done. Indexed: {indexed}, Skipped: {skipped}, Errors: {errors} ({dt:.1f}s)")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
