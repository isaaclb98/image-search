"""
indexer/run_pipeline.py — thin library wrapper around IndexerPipeline.

`local_sync.py` is a feature-rich CLI with change-detection, prune,
and backfill modes that predate the pipeline abstraction. Those
features will migrate to the pipeline as separate phases in
follow-on PRs.

This module exposes the simplest possible "full sync one source via
the pipeline" entry point — no change detection, no prune, no
backfill. It's the right shape for the desktop app's "Index this
folder" action, where there's nothing to diff against.

Caller contract:
  - `source` must be a directory of images the indexer knows how to
    embed (see `indexer.scan:IMAGE_EXTENSIONS`).
  - `qdrant_client` must be an open connection. The caller owns its
    lifecycle.
  - `model_name` must be registered in `image_search_kernel.registry`.
    Defaults to the web backend's current model.
  - `dry_run=True` runs the full pipeline without writing to Qdrant.
    Returns the report's counts unchanged.

Returns a `PipelineReport` (from `indexer.pipeline`).

This module does NOT touch the existing `local_sync.main()` code
path. Both can coexist; `main()` continues to handle change-detection
and the other modes that the pipeline doesn't yet support.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from qdrant_client.http import models as qmodels

from image_search_kernel.payload_schema import FIELD_PATH, SCHEMA_VERSION
from image_search_kernel.registry import Embedder
from image_search_kernel.registry import get as _registry_get
from indexer.pipeline import (
    IndexerPipeline,
    PipelineConfig,
    PipelineReport,
    WriteResult,
)
from indexer.upsert import build_payload, id_for

logger = logging.getLogger(__name__)

# Concurrent PIL decode (§C2). 4 workers by default — the embed
# phase is the bottleneck on CPU and adding more workers just
# queues at the embed stage. Override via `IMAGE_LOAD_POOL_SIZE`
# env var for hosts with more cores.
_LOAD_POOL_SIZE = int(os.environ.get("IMAGE_LOAD_POOL_SIZE", "4"))

__all__ = ["run_pipeline_source"]


# Adapter phase implementations: wrap the production modules so they
# satisfy the pipeline Protocols.

def _scan(source: Path) -> Iterator[Path]:
    from indexer.scan import snapshot
    return iter(snapshot(source))


def _load(paths: Iterator[Path], *, on_failure) -> Iterator[tuple[Path, Any]]:
    """Concurrent PIL decode via a ThreadPoolExecutor (§C2).

    `indexer.image_loader.load` is GIL-released (PIL is mostly C
    code) — threads work and are cheaper than processes. The pool
    prefetches up to `_LOAD_POOL_SIZE` images so the embed phase
    never waits on disk I/O for a single image at a time.

    Failures are reported per-image via `on_failure`, never raised.
    The pipeline contract is unchanged: this is still a sync iterator
    yielding `(Path, Image)` tuples.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from indexer.image_loader import LoaderError, load

    inflight: dict = {}

    def submit_next() -> bool:
        try:
            p = next(paths)
        except StopIteration:
            return False
        inflight[executor.submit(load, p)] = p
        return True

    with ThreadPoolExecutor(max_workers=_LOAD_POOL_SIZE) as executor:
        # Prime the pool.
        for _ in range(_LOAD_POOL_SIZE):
            if not submit_next():
                break

        # Yield as completed; refill one per yield so the pool stays full.
        while inflight:
            future = next(as_completed(inflight))
            p = inflight.pop(future)
            try:
                img = future.result()
                yield (p, img)
            except LoaderError as exc:
                on_failure(p, exc)
            except Exception as exc:  # defensive: PIL's zoo
                on_failure(p, exc)
            submit_next()


def _embed(items: Iterator[tuple[Path, Any]], *, embedder: Embedder) -> Iterator[tuple[Path, Any, list[float]]]:
    for path, image in items:
        vec = embedder.embed_image(image)
        yield (path, image, vec)


def _upsert(
    items, *, client, collection, dry_run, batch_size, on_failure,
) -> Iterator[WriteResult]:
    batch: list = []
    for path, image, vec in items:
        try:
            # The pipeline doesn't pass model_name; use the dim
            # recorded in the embedder for self-describing payloads.
            embedder_dim = getattr(image, "__embedder_dim__", None) or len(vec)
            payload = build_payload(
                path=path, shard="",
                model_name="mock-1536",  # overwritten below via direct registry lookup
                model_revision="test-r0",
                collection=collection,
                model_dim=embedder_dim,
            )
            # build_payload hard-codes model_name='mock-1536' for the
            # _default_ test fixture. For real callers, we override
            # model_name + model_revision here to match the active
            # registry entry.
            payload["model_name"] = _resolve_active_model_name()
            payload["model_revision"] = _resolve_active_model_revision()
            payload["_schema_version"] = SCHEMA_VERSION
            point_id = id_for(path, shard="")
            point = qmodels.PointStruct(
                id=point_id, vector=vec, payload=payload,
            )
            batch.append(point)
            yield WriteResult(
                path=path, point_id=point_id, dry_run=dry_run,
                vector_dim=len(vec),
            )
        except Exception as exc:
            on_failure(path, exc)

    if batch and not dry_run:
        try:
            client.upsert(collection_name=collection, points=batch, wait=False)
        except Exception as exc:
            for pt in batch:
                # Best-effort: report the failure against the source path.
                on_failure(Path(pt.payload.get(FIELD_PATH, "<unknown>")), exc)


_ACTIVE_MODEL: tuple[str, str] | None = None


def _resolve_active_model_name() -> str:
    global _ACTIVE_MODEL
    if _ACTIVE_MODEL is not None:
        return _ACTIVE_MODEL[0]
    # Fallback: the web backend's current model.
    return "ViT-gopt-16-SigLIP2-384"


def _resolve_active_model_revision() -> str:
    global _ACTIVE_MODEL
    if _ACTIVE_MODEL is not None:
        return _ACTIVE_MODEL[1]
    return "webli"


def set_active_model(model_name: str, model_revision: str) -> None:
    """Pin the model_name + revision used in newly-built payloads.

    The pipeline doesn't carry model identity per-call; payloads
    record the active model at index time. Callers (the desktop
    app, the CLI) set this once at startup; subsequent
    `run_pipeline_source` calls record the same identity.
    """
    global _ACTIVE_MODEL
    _ACTIVE_MODEL = (model_name, model_revision)


def run_pipeline_source(
    *,
    source: Path,
    qdrant_client: Any,
    collection: str,
    model_name: str = "ViT-gopt-16-SigLIP2-384",
    batch_size: int = 16,
    dry_run: bool = False,
    cancel_event: threading.Event | None = None,
    on_progress: Any = None,
) -> PipelineReport:
    """Run the new `IndexerPipeline` for a single source directory.

    Returns a `PipelineReport`. Failures are aggregated, not raised.
    Cancelling via `cancel_event` between phases returns a partial
    report.
    """
    embedder = _registry_get(model_name).vision

    pipeline = IndexerPipeline(
        scan=_scan,        # type: ignore[arg-type]
        load=_load,        # type: ignore[arg-type]
        embed=_embed,      # type: ignore[arg-type]
        upsert=_upsert,    # type: ignore[arg-type]
        cancel_event=cancel_event,
    )
    config = PipelineConfig(
        source=source,
        model_name=model_name,
        collection=collection,
        batch_size=batch_size,
        dry_run=dry_run,
        qdrant_client=qdrant_client,
        progress_every=batch_size,
        on_progress=on_progress,
    )
    report = pipeline.run(config)
    logger.info(
        "pipeline finished: upserted=%d failures=%d elapsed=%.2fs",
        report.total_upserted,
        len(report.failures),
        report.elapsed_seconds,
    )
    return report
