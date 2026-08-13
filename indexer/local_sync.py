#!/usr/bin/env python3
"""
local_sync.py — Windows-friendly single-command sync+embed.
Unified: walk source(s) -> diff vs Qdrant -> embed on local GPU -> upsert.
No _pending queue. Tailscale-native.
"""

from __future__ import annotations
import argparse, logging, os, time
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient

from indexer import scan as scan_mod
from indexer import upsert
from indexer.image_loader import letterbox_resize, load
from indexer.vision_encoder import VisionEncoder
from search.qdrant_url import client_kwargs as _qdrant_client_kwargs

load_dotenv()
logger = logging.getLogger(__name__)


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="local_sync", description="Single-command sync+embed for Windows")
    p.add_argument("--source", type=Path, action="append", required=True, help="Folder to scan. Repeatable.")
    p.add_argument("--source-name", type=str, action="append", default=None, dest="source_names", help="Logical name. Repeatable; if one given for many sources, applied to all.")
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--prune", action="store_true")
    p.add_argument("--model", type=str, default="ViT-gopt-16-SigLIP2-384")
    p.add_argument("--device", type=str, default="cuda")
    p.add_argument("--qdrant-url", type=str, default=os.environ.get("QDRANT_URL", "http://localhost:6333"))
    p.add_argument("--qdrant-api-key", type=str, default=os.environ.get("QDRANT_API_KEY") or None)
    p.add_argument("--qdrant-collection", type=str, default=os.environ.get("QDRANT_COLLECTION", "images"))
    p.add_argument("--qdrant-in-memory", action="store_true")
    p.add_argument("--prefix", type=str, default=os.environ.get("PATH_PREFIX", ""))
    p.add_argument("--base", type=str, default=os.environ.get("NAS_IMAGES_BASE", ""))
    p.add_argument(
        "--reblurhash",
        action="store_true",
        help="Walk the existing collection, recompute blurhash for each point from its source "
             "file, and rewrite only the 'blurhash' payload field. Does NOT re-embed. Idempotent. "
             "Mutually exclusive with --refingerprint.",
    )
    p.add_argument(
        "--refingerprint",
        action="store_true",
        help="Same as --reblurhash but for content_sha256 + dhash (diversity / near-duplicate "
             "detection in the search side). Does NOT re-embed. Idempotent. Mutually exclusive "
             "with --reblurhash.",
    )
    return p.parse_args(argv)


def make_client(args):
    if args.qdrant_in_memory:
        return QdrantClient(location=":memory:")
    return QdrantClient(**_qdrant_client_kwargs(url=args.qdrant_url, api_key=args.qdrant_api_key, timeout=30))


def resolve_source_names(sources, names):
    if not names:
        return [src.name for src in sources]
    if len(names) == 1 and len(sources) > 1:
        return names * len(sources)
    if len(names) != len(sources):
        raise ValueError("source-name count mismatch")
    return names


def canonical_payload_path(local_path: Path, prefix: str, base: str) -> str:
    lp = local_path.resolve()
    if prefix and base:
        try:
            base_path = Path(base).resolve()
            try:
                rel = lp.relative_to(base_path)
                return str(Path(prefix) / rel)
            except ValueError:
                lp_s = str(lp)
                base_s = str(base_path)
                if lp_s.lower().startswith(base_s.lower()):
                    rel_s = lp_s[len(base_s):].lstrip("/\\")
                    return str(Path(prefix) / rel_s)
                return str(lp)
        except Exception:
            return str(lp)
    return str(lp)


def main(argv=None):
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    try:
        source_names = resolve_source_names(args.source, args.source_names)
    except ValueError as exc:
        logger.error(str(exc))
        return 2

    for src in args.source:
        if not src.exists():
            logger.error("source does not exist: %s", src)
            return 2
        if not src.is_dir():
            logger.error("source is not a directory: %s", src)
            return 2

    client = make_client(args)
    if not args.dry_run:
        upsert.ensure_collection(client, args.qdrant_collection)
        upsert.ensure_payload_index(client, args.qdrant_collection, "source", "keyword")

    if args.reblurhash and args.refingerprint:
        logger.error("--reblurhash and --refingerprint are mutually exclusive")
        return 2

    encoder = None
    if not args.dry_run and not args.reblurhash and not args.refingerprint:
        # Backfill paths don't embed — they just read source files
        # and `set_payload` a single field. Skip the VisionEncoder
        # init so that --reblurhash / --refingerprint work on machines
        # without a CUDA driver (CI, CPU-only test fixtures, etc).
        encoder = VisionEncoder(arch=args.model, device=args.device)

    total_indexed = 0
    total_skipped = 0
    total_errors = 0
    t0 = time.time()

    if args.reblurhash or args.refingerprint:
        return _backfill_payload_field(
            client=client,
            collection=args.qdrant_collection,
            field="blurhash" if args.reblurhash else "fingerprint",
            sources=args.source,
            source_names=source_names,
            prefix=args.prefix,
            base=args.base,
            batch_size=args.batch_size,
            limit=args.limit,
        )

    # Prune once per run, scoped to the full set of sources this run
    # manages. Doing it inside the per-source loop would let each
    # source's prune delete points owned by the other sources (the
    # alive-set is built from one dir but the scroll covers the whole
    # collection). Walking all source dirs + passing every source-name
    # means only genuinely-missing files under the managed sources are
    # removed — points from unmanaged sources are never touched.
    if args.prune and not args.dry_run:
        removed = upsert.prune_missing(
            client, args.qdrant_collection,
            source_dirs=args.source,
            prefix=args.prefix, base=args.base,
            source_names=source_names,
        )
        logger.info("prune removed %d point(s)", removed)

    for src_path, src_name in zip(args.source, source_names, strict=True):
        logger.info("=== %s -> %s ===", src_path, src_name)
        snap = scan_mod.snapshot(src_path)
        if args.limit:
            snap = snap[:args.limit]
        logger.info("found %d files in %s", len(snap), src_path)
        if not snap:
            continue

        for i in range(0, len(snap), args.batch_size):
            batch = snap[i:i+args.batch_size]
            if args.dry_run:
                logger.info("dry-run would embed %d files", len(batch))
                total_indexed += len(batch)
                continue

            ids = [upsert.id_for(p, "") for p in batch]
            already = upsert.existing_ids(client, args.qdrant_collection, ids)
            new_paths = [p for p, pid in zip(batch, ids, strict=False) if pid not in already]
            total_skipped += len(batch) - len(new_paths)
            if not new_paths:
                continue

            loaded = []
            for p in new_paths:
                try:
                    img = load(p)
                    loaded.append((p, img))
                except Exception as exc:
                    logger.warning("load error %s: %s", p, exc)
                    total_errors += 1

            if not loaded:
                continue

            try:
                vecs = encoder.embed_batch([letterbox_resize(img) for _, img in loaded])
            except Exception:
                logger.exception("embed failed")
                total_errors += len(loaded)
                continue

            items = []
            for (path, _), vec in zip(loaded, vecs, strict=False):
                canon = canonical_payload_path(path, args.prefix, args.base)
                payload = upsert.build_payload(path, "", args.model, "", src_name)
                payload["path"] = canon
                items.append((upsert.id_for(path, ""), vec, payload))

            try:
                upsert.upsert_batch(client, args.qdrant_collection, [(pid, v, pl) for pid, v, pl in items], wait=True)
                total_indexed += len(items)
            except Exception:
                logger.exception("upsert failed")
                total_errors += len(items)

    dt = time.time() - t0
    print(f"Done indexed={total_indexed} skipped={total_skipped} errors={total_errors} ({dt:.1f}s)")
    return 0


def _backfill_payload_field(
    client,
    collection: str,
    field: str,  # "blurhash" or "fingerprint"
    sources: list,
    source_names: list,
    prefix: str,
    base: str,
    batch_size: int,
    limit: int,
) -> int:
    """
    Walk the existing collection, recompute `field` for each point from
    its source file, and `set_payload` only that field in-place. No
    re-embedding; the 1536-dim vector stays untouched.

    Used as a backfill: existing points indexed before [field] support
    was added to the indexer have `field=None` in their payload. The
    client side reads `field` for instant placeholder rendering (LQIP)
    or near-duplicate detection; without it, those features degrade
    silently. This is the cheap way to fix that without re-embedding
    1M+ points.

    For `--reblurhash`: reads the JPEG/PNG/HEIC, downsamples to 32x32,
    encodes to a ~28-char blurhash string. ~30-60s per 1k images on
    a fast disk (most of that is file I/O + Pillow decode).

    For `--refingerprint`: reads the file, computes SHA-256 of the
    bytes and a 64-bit dHash of a tiny downsampled version. Used
    by the diversity ranker in the search side. Cheaper than blurhash
    because the SHA-256 is bytes-only (no decode).

    Source-scoped: only points whose payload.path falls under one of
    the `--source` roots are touched. This is intentional — the Windows
    runs only know about the kpop sources, so a backfill shouldn't
    rewrite points that were indexed by a different machine with
    different path conventions.

    Idempotent: points whose computed value already matches the
    existing payload value are skipped.
    """
    if not sources:
        logger.error("--%s requires at least one --source", field)
        return 2

    if field == "blurhash":
        from indexer.blurhash import compute_blurhash

        def compute(path):
            return compute_blurhash(path)
    elif field == "fingerprint":
        from indexer.fingerprints import compute_fingerprints

        def compute(path):
            fp = compute_fingerprints(path)
            # compute_fingerprints returns the full dict; set_payload
            # expects a single key. We only rewrite the bytes-hash + dhash
            # pair; the 'blurhash' is a separate field.
            return {"content_sha256": fp.get("content_sha256"), "dhash": fp.get("dhash")}
    else:
        logger.error("unknown backfill field %r", field)
        return 2

    total_updated = total_skipped = total_failed = 0
    t0 = time.time()
    offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=collection,
            limit=batch_size * 8,  # scroll a chunk; we filter by source
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            break

        # Filter to points under one of the configured sources.
        # The payload.path is the canonical UNC form; --base + --prefix
        # translate a local file path back to its canonical form so we
        # can match. But the simpler + robust path: take a point's
        # payload.path and check whether it starts with any of our
        # canonical source roots (which we know by --source-name mapping).
        # The source-name field is already in the payload, so the
        # simplest source-scoping is: points whose payload.source is
        # in source_names are the ones to backfill.
        scoped = [p for p in points if (p.payload or {}).get("source") in source_names]
        if not scoped:
            if next_offset is None:
                break
            offset = next_offset
            continue

        for p in scoped:
            payload = p.payload or {}
            path_str = str(payload.get("path") or "")
            if not path_str:
                total_failed += 1
                continue
            try:
                load_path = path_str
                if prefix and base:
                    mapped = canonical_payload_path(Path(path_str), prefix, base)
                    if mapped is not None:
                        load_path = mapped
                # compute() is either (blurhash: str | None) or
                # (fingerprint: dict). For blurhash we need to map the
                # scalar value; for fingerprint we already have a dict.
                computed = compute(Path(load_path))
                if field == "blurhash":
                    new_value = computed  # str | None
                else:
                    new_value = {k: v for k, v in computed.items() if v}
            except Exception as exc:
                logger.warning("backfill: failed to compute %s for %s: %s", field, path_str, exc)
                total_failed += 1
                continue

            existing = payload.get(field)
            if field == "blurhash":
                if existing == new_value:
                    total_skipped += 1
                    continue
            else:
                # fingerprint: dict of {content_sha256, dhash}; skip
                # when both already match.
                if existing and all(existing.get(k) == new_value.get(k) for k in new_value):
                    total_skipped += 1
                    continue

            try:
                client.set_payload(
                    collection_name=collection,
                    payload={field: new_value} if field == "blurhash" else new_value,
                    points=[p.id],
                )
                total_updated += 1
            except Exception as exc:
                logger.warning("backfill: set_payload failed for %s: %s", p.id, exc)
                total_failed += 1

            if limit and total_updated + total_failed >= limit:
                break

        if limit and total_updated + total_failed >= limit:
            break
        if next_offset is None:
            break
        offset = next_offset

    dt = time.time() - t0
    print(
        f"backfill[{field}] done updated={total_updated} skipped={total_skipped} "
        f"failed={total_failed} ({dt:.1f}s)"
    )
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
