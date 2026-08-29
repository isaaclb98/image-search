#!/usr/bin/env python3
"""
local_sync.py — Windows-friendly single-command sync+embed.
Unified: walk source(s) -> diff vs Qdrant -> embed on local GPU -> upsert.
No _pending queue. Tailscale-native.

When invoked by the search backend (via the admin Index API), the
runner spawns this module as a child process and reads its stdout for
structured progress. Two flags wire that up:

  --json-progress   emit one JSON line per progress event to stdout
                    (alongside the human-readable logger output)
  --rebuild         wipe the Qdrant collection first, then embed every
                    source file (no change-detection skip)

Cancel is cooperative: SIGTERM sets an internal cancellation flag that
the main loop checks at every batch boundary. The process exits cleanly
with code 130 and emits a {"event": "cancelled"} progress line.
"""  # noqa: EXE001

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient

from image_search_kernel.qdrant_url import client_kwargs as _qdrant_client_kwargs
from image_search_kernel.registry import get as _registry_get
from indexer import scan as scan_mod
from indexer import upsert
from indexer.image_loader import letterbox_resize, load_image_pil, peek_source_dims
from indexer.retry import RetryExhausted, retry_with_backoff
from indexer.thumbnails import generate_thumbnail_for_path
from indexer.vision_encoder import VisionEncoder

load_dotenv()
logger = logging.getLogger(__name__)


# --- Cancellation support ------------------------------------------------
# SIGTERM sets `_cancel_event`; the main loop checks it at every batch
# boundary and exits cleanly. Initialised lazily inside `main` so module
# import has no side effects (tests import this file repeatedly).

_cancel_event: threading.Event | None = None


def _install_signal_handlers() -> None:
    """Wire SIGTERM (and SIGINT, for dev Ctrl-C) to the cancel event."""
    global _cancel_event
    _cancel_event = threading.Event()

    def _on_signal(signum, _frame):
        if _cancel_event is not None:
            _cancel_event.set()
            logger.info("received signal %d; cancelling at next batch boundary", signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            # Signal handlers can only be installed from the main thread.
            # Spawned subprocess workers (rare) skip this silently.
            pass


def _is_cancelled() -> bool:
    return _cancel_event is not None and _cancel_event.is_set()


# --- JSON progress output ------------------------------------------------
# When --json-progress is on, we emit ONE JSON object per line to stdout
# for the runner to parse. Logger output is untouched (goes to stderr
# in non-interactive runs) so the runner can split the streams cleanly.

_json_progress: bool = False


def _emit_progress(payload: dict) -> None:
    """Write one JSON progress line to stdout if --json-progress is on."""
    if not _json_progress:
        return
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


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
    p.add_argument(
        "--full",
        action="store_true",
        help="Full sweep: embed new/changed files, backfill missing "
        "blurhash+fingerprint on legacy points, prune dead points.",
    )
    p.add_argument(
        "--rebuild",
        action="store_true",
        help="Wipe the Qdrant collection before indexing, then embed every "
        "source file (no change-detection skip). Used by the in-app Index "
        "Rebuild-from-scratch mode. Mutually exclusive with --reblurhash "
        "and --refingerprint.",
    )
    p.add_argument(
        "--json-progress",
        action="store_true",
        help="Emit one JSON object per progress event to stdout. Used by the "
        "admin Index runner to track job state. Has no effect on the "
        "human-readable logger output.",
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


def _await_points_visible(client, name, ids, timeout_s=30.0, poll_s=0.2):
    """Poll until every id in `ids` is retrievable from the collection.

    Batched upserts are sent with wait=False; Qdrant acknowledges them
    asynchronously. This bounds the race for callers that read right
    after sync. Returns True when visible, False on timeout.
    """
    deadline = time.time() + timeout_s
    want = set(ids)
    while True:
        try:
            found = upsert.existing_ids(client, name, list(want))
        except Exception:  # noqa: BLE001
            found = set()
        if want <= found:
            return True
        if time.time() >= deadline:
            logger.warning(
                "timed out waiting for %d point(s) to become visible",
                len(want - found),
            )
            return False
        time.sleep(poll_s)


def main(argv=None):
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    # Wire subprocess-level concerns (cancel signals, JSON progress) BEFORE
    # any work begins. Both are no-ops if their respective flags are off.
    global _json_progress
    _json_progress = bool(args.json_progress)
    _install_signal_handlers()

    # Mutual-exclusion checks (rebuild is the new entrant).
    if args.rebuild and (args.reblurhash or args.refingerprint):
        logger.error("--rebuild is mutually exclusive with --reblurhash/--refingerprint")
        return 2
    if args.rebuild and args.full:
        logger.error("--rebuild implies a fresh index; do not combine with --full")
        return 2

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
        # Resolve the collection's vector dim from the chosen model so a
        # non-default --model (e.g. ViT-L-16-SigLIP2-256) gets a
        # 1024-dim collection instead of upsert.ensure_collection's
        # hardcoded 1536 (the gopt default).
        model_dim = _registry_get(args.model).dim

        if args.rebuild:
            # Wipe-and-rebuild: drop the collection, then recreate via
            # ensure_collection. Done BEFORE the progress "start" event so
            # the runner's points_count reflects the post-wipe state (zero).
            try:
                client.delete_collection(collection_name=args.qdrant_collection)
                logger.info("rebuild: deleted existing collection %r", args.qdrant_collection)
            except Exception as exc:  # noqa: BLE001 — first run, collection may not exist
                logger.info("rebuild: no existing collection to delete (%s)", exc)

        upsert.ensure_collection(client, args.qdrant_collection, dim=model_dim)
        upsert.ensure_payload_index(client, args.qdrant_collection, "collection", "keyword")

    if args.reblurhash and args.refingerprint:
        logger.error("--reblurhash and --refingerprint are mutually exclusive")
        return 2

    if args.full and (args.reblurhash or args.refingerprint):
        logger.error("--full includes backfill; drop --reblurhash/--refingerprint")
        return 2

    # Lazy: encoder only built when we actually embed. Backfill
    # (--reblurhash / --refingerprint)and --prune / --dry-run paths
    # never embed, so those runs must not pay the multi-second
    # VisionEncoder (timm ViT) init cost — it made prune tests hang
    # on CPU-only machines.
    encoder: VisionEncoder | None = None


    total_indexed = 0


    total_reembedded = 0


    last_written_ids: list = []
    total_skipped = 0
    total_errors = 0
    t0 = time.time()

    # Emit the "start" event for the runner. Doing it here (after the
    # rebuild wipe + collection re-create) so the runner sees a fresh
    # points_count from the very first status poll.
    _emit_progress({
        "event": "start",
        "mode": "rebuild" if args.rebuild else "incremental",
        "model": args.model,
        "device": args.device,
        "sources": [str(s) for s in args.source],
        "batch_size": args.batch_size,
    })

    if args.reblurhash or args.refingerprint:
        return _backfill_payload_field(
            client=client,
            collection=args.qdrant_collection,
            field="blurhash" if args.reblurhash else "fingerprint",
            sources=args.source,
            source_names=source_names,
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
            source_names=source_names,
        )
        logger.info("prune removed %d point(s)", removed)

    for src_path, src_name in zip(args.source, source_names, strict=True):
        logger.info("=== %s -> %s ===", src_path, src_name)
        # Estimate the total for this source from Qdrant's per-source
        # point count (cheap: filtered count against the `source`
        # payload index) so the walk progress can show a time-to-go.
        expected_total = None
        if not args.dry_run:
            try:
                from qdrant_client.http import models as _qm
                cnt = client.count(
                    collection_name=args.qdrant_collection,
                    count_filter=_qm.Filter(
                        must=[
                            _qm.FieldCondition(
                                key="collection",
                                match=_qm.MatchValue(value=src_name),
                            )
                        ]
                    ),
                    exact_count=False,
                )
                expected_total = int(cnt.count)
            except Exception as exc:  # noqa: BLE001
                logger.debug("count for %s failed: %s", src_name, exc)
        snap = scan_mod.snapshot(src_path, expected_total=expected_total)
        if args.limit:
            snap = snap[:args.limit]
        logger.info("found %d files in %s", len(snap), src_path)
        if not snap:
            continue

        for i in range(0, len(snap), args.batch_size):
            batch = snap[i:i+args.batch_size]

            # Cancel check at batch boundary. SIGTERM (from the runner)
            # sets _cancel_event; we bail BEFORE loading or embedding
            # another batch and emit a final "cancelled" event.
            if _is_cancelled():
                logger.info("cancel requested; stopping after %d indexed", total_indexed)
                _emit_progress({
                    "event": "cancelled",
                    "indexed": total_indexed,
                    "reembedded": total_reembedded,
                    "skipped": total_skipped,
                    "errors": total_errors,
                    "duration_s": time.time() - t0,
                })
                return 130

            # Skip the change-detection scroll entirely in --rebuild
            # mode. We just wiped the collection, so every existing id
            # lookup would fail anyway — and even if it didn't, we want
            # to embed every file from scratch.
            if args.rebuild:
                to_embed = [(p, "new") for p in batch]
                n_new = len(batch)
                n_changed = 0
            else:
                ids = [upsert.id_for(p) for p in batch]
                # Change detection: pull mtime/size for points that already
                # exist. Point ids are deterministic (upsert.id_for), so a
                # changed file re-embeds INTO its existing point — no
                # duplicates, favourites/album membership preserved.
                # Points lacking stored mtime/size (pre-change-detection
                # index) are treated as changed so they heal on next run.
                existing_meta: dict = {}
                try:
                    points = client.retrieve(
                        collection_name=args.qdrant_collection,
                        ids=ids,
                        with_payload=["mtime", "size"],
                    )
                    for pt in points:
                        pl = pt.payload or {}
                        if pl.get("mtime") is not None and pl.get("size") is not None:
                            existing_meta[str(pt.id)] = (int(pl["mtime"]), int(pl["size"]))
                        else:
                            existing_meta[str(pt.id)] = None
                except Exception:
                    if not args.dry_run:
                        raise
                    logger.warning("dry-run: could not read existing points; assuming all new")
                    existing_meta = {}

                to_embed: list = []
                n_new = 0
                n_changed = 0
                for path, pid in zip(batch, ids, strict=False):
                    if pid not in existing_meta:
                        to_embed.append((path, "new"))
                        n_new += 1
                        continue
                    recorded = existing_meta[pid]
                    if recorded is None:
                        to_embed.append((path, "changed"))
                        n_changed += 1
                        continue
                    try:
                        st = path.stat()
                    except OSError:
                        total_errors += 1
                        continue
                    if int(st.st_mtime) != recorded[0] or int(st.st_size) != recorded[1]:
                        to_embed.append((path, "changed"))
                        n_changed += 1
                    else:
                        total_skipped += 1

            logger.info(
                "batch %d: %d new, %d changed, %d up-to-date",
                i // args.batch_size + 1, n_new, n_changed,
                len(batch) - n_new - n_changed,
            )

            if not to_embed:
                continue

            if args.dry_run:
                logger.info(
                    "dry-run: would embed %d file(s) (%d new, %d changed)",
                    len(to_embed), n_new, n_changed,
                )
                total_indexed += n_new
                total_reembedded += n_changed
                continue

            loaded = []
            for p, _reason in to_embed:
                try:
                    # Round-30: capture source dims from the JPEG header
                    # (cheap; a few KB) and load the ORIGINAL image, not
                    # the letterboxed one. The thumbnail generator needs
                    # the original — passing it the letterboxed input
                    # bakes the model's black padding bars into the
                    # 256x256 WebP (see regression test in
                    # tests/test_thumbnails_no_letterbox.py).
                    source_w, source_h = peek_source_dims(p)
                    img = load_image_pil(p)
                    loaded.append((p, img, source_w, source_h))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("load error %s: %s", p, exc)
                    total_errors += 1

            if not loaded:
                continue

            try:
                if encoder is None:
                    # First-batch model load. Downloading the SigLIP2
                    # weights from HF Hub takes 30-60s on a fresh
                    # install; without this event the UI sits at
                    # "Running · 0 indexed" the whole time and looks
                    # frozen. Emitting "warming_up" before the load
                    # starts gives the UI a clear state.
                    _emit_progress({"event": "warming_up", "model": args.model})
                    encoder = VisionEncoder(arch=args.model, device=args.device)

                vecs = encoder.embed_batch([letterbox_resize(img) for (_, img, _sw, _sh) in loaded])
            except Exception:
                logger.exception("embed failed")
                total_errors += len(loaded)
                continue

            items = []
            for (path, img, _source_w, _source_h), vec in zip(
                loaded, vecs, strict=False,
            ):
                # Generate thumbnail (best-effort, non-fatal)
                try:
                    upsert.id_for(path, "")
                    thumb_path = generate_thumbnail_for_path(img, path, "")
                    if thumb_path:
                        logger.debug(f"Generated thumbnail: {thumb_path}")
                except (OSError, ValueError, RuntimeError) as e:
                    # Thumbnail generation can fail on corrupt JPEGs,
                    # PIL decode errors, or filesystem issues. The
                    # caller wants the index to continue regardless;
                    # narrow to the realistic failure modes rather than
                    # a blind `Exception` catch.
                    logger.warning(f"Failed to generate thumbnail for {path}: {e}")

                canon = path
                # Round‑30: local_sync's full-sweep backfill still
                # uses the simple `build_payload` signature — dims
                # would require an extra PIL.Image.read() per file.
                # Skip source dims here; the regular ingest pipeline
                # populates them, and any photo indexed via
                # local_sync will be re-embedded by the next
                # run_pipeline_source call (which DOES populate
                # dims). Trade-off: photos that were only ever
                # touched by local_sync show "—" on the photo
                # page until a regular ingest re-runs.
                payload = upsert.build_payload(path, "", args.model, "", src_name)
                payload["path"] = canon
                items.append((upsert.id_for(path, ""), vec, payload))

            try:
                # Wrap the upsert in retry-with-backoff. A single transient
                # Qdrant failure (5xx, network blip) must not abort the
                # whole job — 3 attempts, 2s/4s/8s between, then fail.
                retry_with_backoff(
                    lambda items=items: upsert.upsert_batch(
                        client,
                        args.qdrant_collection,
                        [(pid, v, pl) for pid, v, pl in items],
                        wait=False,
                    ),
                    max_attempts=3,
                    base_delay_s=2.0,
                )
                total_indexed += sum(1 for (_p, r) in to_embed if r == "new")
                total_reembedded += sum(1 for (_p, r) in to_embed if r == "changed")
                last_written_ids = [iid for (iid, _v, _pl) in items]
                # Emit per-batch progress so the runner can show live counts.
                _emit_progress({
                    "event": "batch",
                    "batch_index": i // args.batch_size + 1,
                    "indexed": total_indexed,
                    "reembedded": total_reembedded,
                    "skipped": total_skipped,
                    "errors": total_errors,
                })
            except RetryExhausted as exc:
                # All retries failed — surface to the runner via a final
                # "failed" event and exit non-zero. The runner records
                # `last_error` from the event payload.
                logger.exception("upsert failed after retries")
                total_errors += len(items)
                _emit_progress({
                    "event": "failed",
                    "stage": "upsert",
                    "error": f"{type(exc.last_exc).__name__}: {exc.last_exc}",
                    "indexed": total_indexed,
                    "reembedded": total_reembedded,
                    "skipped": total_skipped,
                    "errors": total_errors,
                    "duration_s": time.time() - t0,
                })
                return 1
            except Exception:
                logger.exception("upsert failed")
                total_errors += len(items)

    # Visibility guarantee: batched upserts go out wait=False for

    # speed; before declaring done, block until the last written

    # batch is actually retrievable so callers never see stale state.

    if args.full and not args.dry_run:
        logger.info("=== full sweep: backfill blurhash ===")
        rc_b1 = _backfill_payload_field(
            client=client,
            collection=args.qdrant_collection,
            field="blurhash",
            sources=args.source,
            source_names=source_names,
            batch_size=args.batch_size,
            limit=0,
        )
        logger.info("=== full sweep: backfill fingerprint ===")
        rc_b2 = _backfill_payload_field(
            client=client,
            collection=args.qdrant_collection,
            field="fingerprint",
            sources=args.source,
            source_names=source_names,
            batch_size=args.batch_size,
            limit=0,
        )
        if rc_b1 or rc_b2:
            logger.warning("full sweep: some backfill rows failed; see log")
        removed = upsert.prune_missing(
            client, args.qdrant_collection,
            source_dirs=args.source,
            source_names=source_names,
        )
        logger.info("full sweep: prune removed %d point(s)", removed)
    elif args.full and args.dry_run:
        logger.info("dry-run --full: backfill and prune skipped (no writes)")

    if last_written_ids and not args.dry_run:

        _await_points_visible(client, args.qdrant_collection, last_written_ids)


    dt = time.time() - t0
    print(f"Done indexed={total_indexed} re-embedded={total_reembedded} skipped={total_skipped} errors={total_errors} ({dt:.1f}s)")
    _emit_progress({
        "event": "done",
        "indexed": total_indexed,
        "reembedded": total_reembedded,
        "skipped": total_skipped,
        "errors": total_errors,
        "duration_s": dt,
    })
    return 0


def _backfill_payload_field(
    client,
    collection: str,
    field: str,  # "blurhash" or "fingerprint"
    sources: list,
    source_names: list,
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
        # The payload.path is the file path the indexer stored (absolute
        # host path). Backfill just reads from that path directly — no
        # translation needed because the indexer runs on the same host
        # as the backfill.
        scoped = [p for p in points if (p.payload or {}).get("collection") in source_names]
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
                # payload.path is the absolute host path the indexer
                # stored; the backfill reads from it directly.
                computed = compute(Path(path_str))
                if field == "blurhash":
                    new_value = computed  # str | None
                else:
                    new_value = {k: v for k, v in computed.items() if v}
            except Exception as exc:  # noqa: BLE001
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
            except Exception as exc:  # noqa: BLE001
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
