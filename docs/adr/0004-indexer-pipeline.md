# ADR-0004: Pull-based indexer pipeline with explicit phase Protocols

**Status:** Accepted.
**Date:** 2026-08.
**Related:** `docs/archive/backend-refactor-plan.md` (archived) §4.4, §4.4.1, §B1.

## Context

`indexer/local_sync.py` was a 540-line module whose `main()` function was a 200-line inline loop: walk the source, diff against Qdrant, embed, batch-upsert, log progress. The loop is the production indexer; it has accumulated feature-creep (change-detection, prune, backfill, dry-run). It is also tightly coupled to FastAPI-free sync execution — the desktop product needs the same indexing logic but cannot import `local_sync` (it brings CLI arg parsing, progress-formatting, and feature modes the desktop doesn't want).

The desktop product (§3 of the plan) needs a library entry point that takes a source directory and produces a vector collection. No CLI args, no change-detection against prior state, no log formatting. Just: walk → load → embed → upsert, with progress and a final report.

## Decision

Introduce `indexer/pipeline.py` with:

- **Four runtime-checkable Protocols** — `ScanPhase`, `LoadPhase`, `EmbedPhase`, `UpsertPhase`. Each is a sync iterator that pulls from the previous phase's output.
- **Three event types** — `ProgressEvent` (phase, count, rate, eta), `PipelineFailure` (phase, path, error), `WriteResult` (path, point_id, dry_run, vector_dim).
- **Two report types** — `PipelineReport` (aggregate counters + failures + elapsed), `PipelineConfig` (source, model_name, collection, batch_size, dry_run, qdrant_client, progress_every, on_progress).
- **`IndexerPipeline` driver** — single sync loop, threading.Event-based cancellation between phases and between upsert batches. Failures are aggregated, not raised. Returns a `PipelineReport`.

Phase adapters in `indexer/run_pipeline.py` wrap the production modules (`indexer.scan.snapshot`, `indexer.image_loader.load`, `indexer.upsert.build_payload` + `id_for`, plus the registered embedder). The wrapper exposes `run_pipeline_source(source, qdrant_client, collection, model_name, batch_size, dry_run, cancel_event, on_progress)` — the simple "index one source directory" use case the desktop app needs.

`local_sync.main()` is **not** rewritten. Its change-detection, prune, backfill, and CLI-arg handling predate the pipeline abstraction and migrate to it as separate phases in follow-on PRs. Both paths coexist; the desktop app uses `run_pipeline_source`, the CLI uses `main()`.

## Properties pinned by this ADR

- **Sync at the phase boundary.** Each phase is a sync iterator. The desktop app wraps the pipeline in a worker thread or child process; the pipeline itself doesn't need to be async.
- **Pull, not push.** Each phase decides when to ask for the next item. Dropping the iterator drops the whole pipeline cleanly.
- **Failure is reported, not raised mid-stream.** Each phase takes an `on_failure` callback. The driver aggregates into a `PipelineReport`. The pipeline completes; the CLI exits non-zero with the report.
- **Progress is a typed event stream**, not a callback. `ProgressEvent`s are emitted at configured boundaries. The CLI prints; the desktop app renders a progress bar.
- **Cancellation is `threading.Event`-based.** The driver checks between phases and between batches in the upsert phase. Partial upserts are NOT committed (the next batched upsert is the boundary).

## Consequences

- **Positive:** The desktop product's indexer entry point is `run_pipeline_source(...)` — no FastAPI, no CLI args, no feature modes it doesn't want.
- **Positive:** Adding a new phase (e.g. thumbnails, EXIF enrichment) is a new Protocol implementation plus a wire in the driver. Existing phases don't change.
- **Positive:** Per-phase concurrency is configured independently. Concurrent PIL decode (planned §C2) wraps the load phase in a `ThreadPoolExecutor`; the driver itself stays single-threaded.
- **Negative:** Two indexer entry points exist now: `local_sync.main()` (legacy CLI) and `run_pipeline_source(...)` (new library). Maintenance cost is small — the legacy path is feature-rich, the library path is simple — but it's two surfaces to think about.
- **Negative:** `IndexerPipeline.run()` is single-threaded by default. CPU-bound work like the embedder runs serially through the phase chain. Phase C2 (concurrent PIL decode) and C1 (async pipeline) are the next steps; the driver is shaped to accommodate them without API changes.

## Alternatives considered

- **Async pipeline.** Rejected for v1: every phase is sync (PIL, model forward, Qdrant IO), and forcing async complicates each phase. The desktop wraps in async at the boundary instead.
- **Class per phase with subclasses.** Rejected: the Protocol approach lets callers compose phases from any module. Subclassing bakes the composition in.
- **Reuse `local_sync.main()` directly from the desktop.** Rejected: the desktop product wants a library, not a CLI. The library form has to be separate.

## Verification

- `tests/test_pipeline.py`: 12/12 passing (Protocol shapes, driver behavior, cancellation, idempotency, per-instance state).
- `tests/test_pipeline_integration.py`: 3/3 passing (real indexer modules wired into the Protocols; end-to-end with in-memory Qdrant; corrupt-file tolerance; dry-run).
- `tests/test_run_pipeline.py`: 7/7 passing (`run_pipeline_source` end-to-end; progress; cancellation; empty source; active-model pin).
- `tests/test_architecture.py`: 7/7 still passing (no dep-direction regressions from the new modules).
