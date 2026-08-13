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

    encoder = None
    if not args.dry_run:
        encoder = VisionEncoder(arch=args.model, device=args.device)

    total_indexed = 0
    total_skipped = 0
    total_errors = 0
    t0 = time.time()

    for src_path, src_name in zip(args.source, source_names, strict=True):
        logger.info("=== %s -> %s ===", src_path, src_name)
        snap = scan_mod.snapshot(src_path)
        if args.limit:
            snap = snap[:args.limit]
        logger.info("found %d files in %s", len(snap), src_path)
        if not snap:
            continue
        if args.prune and not args.dry_run:
            removed = upsert.prune_missing(client, args.qdrant_collection, source_dirs=[src_path])
            logger.info("prune removed %d", removed)

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


if __name__ == "__main__":
    import sys
    sys.exit(main())
