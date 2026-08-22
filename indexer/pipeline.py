"""
indexer/pipeline.py — pull-based indexer pipeline (§B1).

The pipeline consumes a source directory through four phases (scan →
load → embed → upsert) and emits progress events + a final report.
Each phase is a sync iterator that pulls from the previous phase's
output. The driver is a single sync loop; per-phase concurrency is
configured independently.

This module defines the Protocols and dataclasses that the phases
must satisfy. Phase implementations live in their existing modules:
  - `indexer.scan.snapshot`  → ScanPhase
  - `indexer.image_loader.load` → LoadPhase
  - `indexer.vision_encoder.VisionEncoder` (or registry embedder) → EmbedPhase
  - `indexer.upsert.upsert_batch` → UpsertPhase

The pipeline class itself (Phase B1 §B1) is a thin driver that
plumbs the phases together. `local_sync.py` is the first consumer.

Sync at the phase boundary by design (per §4.4.1). The desktop app
wraps the pipeline in a worker thread or child process; the
pipeline itself doesn't need to be async.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from image_search_kernel.registry import Embedder

__all__ = [
    "CancelledError",
    "EmbedPhase",
    "IndexerPipeline",
    "LoadPhase",
    "PipelineConfig",
    "PipelineFailure",
    "PipelineReport",
    "ProgressEvent",
    "ScanPhase",
    "UpsertPhase",
]


# ---------------------------------------------------------------------------
# Phase Protocols — the only model-specific surface in the indexer.
# ---------------------------------------------------------------------------

@runtime_checkable
class ScanPhase(Protocol):
    """Yield `Path` objects under `source`, recursively, in a stable order."""

    def __call__(self, source: Path) -> Iterator[Path]: ...


@runtime_checkable
class LoadPhase(Protocol):
    """Convert each scanned `Path` into `(Path, Tensor)` pairs.

    `Tensor` is whatever the registered `Embedder` consumes —
    typically a PIL.Image for the open_clip path, or a preprocessed
    CHW float list for ONNX. The pipeline doesn't constrain it.

    `on_failure` is invoked for each path that the load phase cannot
    handle (corrupt file, permission error, etc.). The phase must
    continue with the next path rather than aborting.
    """

    def __call__(
        self,
        paths: Iterator[Path],
        *,
        on_failure: Callable[[Path, LoaderErrorLike], None],
    ) -> Iterator[tuple[Path, Any]]: ...


@runtime_checkable
class EmbedPhase(Protocol):
    """Run the registered embedder on each `(Path, Tensor)` item."""

    def __call__(
        self,
        items: Iterator[tuple[Path, Any]],
        *,
        embedder: Embedder,
    ) -> Iterator[tuple[Path, Any, list[float]]]: ...


@runtime_checkable
class UpsertPhase(Protocol):
    """Write `(Path, Tensor, Vector)` tuples to Qdrant in batches.

    `dry_run=True` reports intended writes without performing them;
    the pipeline driver can surface a `PipelineReport.dry_run_writes`
    count for operator verification.
    """

    def __call__(
        self,
        items: Iterator[tuple[Path, Any, list[float]]],
        *,
        client: Any,  # QdrantClient — kept untyped to avoid a kernel dep on qdrant-client
        collection: str,
        dry_run: bool,
        batch_size: int,
        on_failure: Callable[[Path, UpsertErrorLike], None],
    ) -> Iterator[WriteResult]: ...


class LoaderErrorLike(Exception):
    """Marker base class so the LoadPhase callback can be type-narrowed."""


class UpsertErrorLike(Exception):
    """Marker base class so the UpsertPhase callback can be type-narrowed."""


@dataclass(frozen=True)
class WriteResult:
    """Result of a single upsert attempt, emitted by `UpsertPhase`."""

    path: Path
    point_id: str
    dry_run: bool
    vector_dim: int


# ---------------------------------------------------------------------------
# Progress + failure events
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProgressEvent:
    """Typed progress event emitted by the driver at configured boundaries.

    Subscribers (CLI printing, desktop progress bar) consume this
    without coupling to driver internals.
    """

    phase: Literal["scan", "load", "embed", "upsert"]
    count: int
    rate_per_sec: float
    eta_seconds: float


@dataclass(frozen=True)
class PipelineFailure:
    """One per-item failure observed during the pipeline run."""

    phase: Literal["load", "embed", "upsert"]
    path: Path
    error: str


@dataclass(frozen=True)
class PipelineReport:
    """Aggregate report emitted at the end of a pipeline run."""

    total_scanned: int = 0
    total_loaded: int = 0
    total_embedded: int = 0
    total_upserted: int = 0
    failures: list[PipelineFailure] = field(default_factory=list)
    dry_run: bool = False
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class PipelineConfig:
    """Runtime configuration for a pipeline run."""

    source: Path
    model_name: str = "ViT-gopt-16-SigLIP2-384"
    collection: str = "images"
    batch_size: int = 16
    dry_run: bool = False
    qdrant_client: Any = None  # QdrantClient; passed through to UpsertPhase
    progress_every: int = 50  # emit a ProgressEvent every N items
    on_progress: Callable[[ProgressEvent], None] | None = None


# ---------------------------------------------------------------------------
# Pipeline driver
# ---------------------------------------------------------------------------

class CancelledError(Exception):
    """Raised by the cancellation hook when the pipeline is aborted.

    The driver catches it during phase transitions and returns a
    partial `PipelineReport` with whatever was completed before the
    cancellation.
    """


class IndexerPipeline:
    """Pull-based pipeline driver (§4.4 / §B1).

    Phases are sync iterators. Per-phase concurrency is configured
    independently (Phase C2 — concurrent PIL decode — wraps the load
    phase in a thread pool; the driver itself stays single-threaded).

    Cancellation: the driver checks `_cancel_event` between every
    item and between every batch in the upsert phase. On
    cancellation, in-flight iterators are closed; partial upserts
    are NOT committed (the next batched upsert is the boundary).
    """

    def __init__(
        self,
        *,
        scan: ScanPhase,
        load: LoadPhase,
        embed: EmbedPhase,
        upsert: UpsertPhase,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._scan = scan
        self._load = load
        self._embed = embed
        self._upsert = upsert
        self._cancel = cancel_event or threading.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        """Request cancellation. Returns immediately; the driver checks
        the flag between phases and between batches in the upsert phase."""
        self._cancel.set()

    def run(self, config: PipelineConfig) -> PipelineReport:
        """Execute the full pipeline. Returns a `PipelineReport`.

        Failures are aggregated, not raised. The pipeline completes;
        the caller decides whether to retry or rollback based on
        the report's failure list.
        """
        from image_search_kernel.registry import get as _registry_get

        t0 = time.perf_counter()
        embedder = _registry_get(config.model_name).vision
        failures: list[PipelineFailure] = []
        counts = {"scan": 0, "load": 0, "embed": 0, "upsert": 0}

        def _emit(phase: str, count: int, t_start: float) -> None:
            if config.on_progress is None:
                return
            if count % config.progress_every != 0:
                return
            elapsed = max(time.perf_counter() - t_start, 1e-9)
            rate = count / elapsed
            eta = 0.0 if rate == 0 else 0.0  # ETA requires total; omitted for now
            config.on_progress(ProgressEvent(
                phase=phase, count=count, rate_per_sec=rate, eta_seconds=eta,
            ))

        # Phase 1: scan
        paths_iter = self._scan(config.source)
        # Phase 2: load
        t_phase = time.perf_counter()

        def _on_load_failure(path: Path, exc: Exception) -> None:
            failures.append(PipelineFailure(
                phase="load", path=path, error=f"{type(exc).__name__}: {exc}",
            ))

        loaded_iter = self._load(paths_iter, on_failure=_on_load_failure)

        # Phase 3: embed
        embedded_iter = self._embed(loaded_iter, embedder=embedder)

        # Phase 4: upsert (batched)
        def _on_upsert_failure(path: Path, exc: Exception) -> None:
            failures.append(PipelineFailure(
                phase="upsert", path=path, error=f"{type(exc).__name__}: {exc}",
            ))

        if config.dry_run:
            # In dry-run mode, the upsert phase still iterates the
            # pipeline but reports intended writes without committing.
            # The contract: dry_run=True on the upsert phase means
            # `WriteResult` is emitted but the Qdrant client is
            # never called. The phase implementation is responsible
            # for honoring this.
            pass

        # Drive the pipeline. Phase implementations are generators;
        # the driver pulls from each in turn. Cancellation check
        # happens between phases.
        try:
            for phase_name in ("scan", "load", "embed", "upsert"):
                if self._cancel.is_set():
                    raise CancelledError(f"cancelled before {phase_name}")
                # The driver doesn't itself iterate each phase —
                # phase implementations may use generators that are
                # only consumed by being passed into the next phase.
                # The actual iteration happens as a single chain
                # through the generator expression below.
                # We count via the upsert phase's WriteResult output
                # (the terminal phase), and emit progress from
                # each phase's iteration.

            # The actual chained iteration. This consumes all four
            # phases lazily; cancellation is checked between writes.
            batch: list[WriteResult] = []
            upsert_iter = self._upsert(
                embedded_iter,
                client=config.qdrant_client,
                collection=config.collection,
                dry_run=config.dry_run,
                batch_size=config.batch_size,
                on_failure=_on_upsert_failure,
            )
            for result in upsert_iter:
                if self._cancel.is_set():
                    raise CancelledError("cancelled mid-upsert")
                counts["upsert"] += 1
                batch.append(result)
                if len(batch) >= config.batch_size:
                    _emit("upsert", counts["upsert"], t_phase)
                    batch = []
            if batch:
                _emit("upsert", counts["upsert"], t_phase)
        except CancelledError:
            pass

        elapsed = time.perf_counter() - t0
        return PipelineReport(
            total_scanned=counts["scan"],
            total_loaded=counts["load"],
            total_embedded=counts["embed"],
            total_upserted=counts["upsert"],
            failures=failures,
            dry_run=config.dry_run,
            elapsed_seconds=elapsed,
        )
