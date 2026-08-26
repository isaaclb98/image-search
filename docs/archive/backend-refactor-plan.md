# Backend refactor, extensibility, and performance plan

**Purpose.** Refactor the existing FastAPI + Qdrant backend so it serves as a
solid foundation for both the current web product and a future Electron +
TypeScript desktop product that will ship alongside it. The desktop app is a
parallel product (independent data directory, independent lifecycle, independent
distribution), not a wrapper around the web backend.

This document specifies **what** the target state is and **what** must be done
to get there. It does not prescribe implementation.

---

## 1. Scope and non-goals

### In scope

- The Python backend (`search/`) and indexer (`indexer/`).
- The shared code that both depend on (extracted during this work).
- Performance, extensibility, modularity, and organization.
- Test and benchmark coverage needed to make the above durable.

### Out of scope

- Frontend (`frontend/`). Existing UI work continues independently.
- The Electron desktop product itself. This plan produces the foundation it
  will sit on, not the desktop app.
- Deployment topology (Docker, k8s, gitops). The Docker image and statefulset
  continue to work through every phase; refactors must not break them.
- New user-visible features (albums, favorites, etc. behave as today).
- Model selection for either product (a separate decision, deferred until
  the desktop product's model registry entry is added).

---

## 2. Current shape and why it's a barrier

The backend works. The audit identified the barriers; this section restates
them at the level this plan addresses:

1. The API layer is one file. 39 routes, ~3400 lines, one factory. Adding a
   route touches unrelated code.
2. The indexer imports from `search/`. The indexer is a separate process
   (runs where the NAS is mounted and the GPU lives). It cannot be shipped
   without `search/`, and any change to `search/qdrant_url.py` can silently
   break it.
3. The Qdrant point payload schema has no version field. A model swap, a
   field rename, or a value-format change is a wipe-and-reindex.
4. Model dimension, resolution, and tag are hardcoded as constants in
   multiple files. A new model is a multi-file grep-and-replace with no
   compile-time check.
5. Indexer-side cache is a hand-rolled JSON file. No atomic-write guarantee
   beyond temp-file + rename, no integrity check, no transaction support,
   no concurrency.
6. Search-side cache (`index_db.py`) is synchronous SQLite, with all I/O on
   the event loop wrapped in `asyncio.to_thread`. Works at one gunicorn
   worker; saturates the thread pool as soon as concurrency increases.
7. The indexer critical-path modules (`upsert.py`, `image_loader.py`,
   `cache.py`, `vision_encoder.py`) lack unit tests. Performance
   refactors on these are unsafe.
8. PIL image decode runs serially on the main thread. The indexer is the
   slowest user-facing operation and decode is a documented bottleneck
   when the embedder is fast.

Each numbered item above maps to a numbered work item in §4.

---

## 3. Target architecture

The backend becomes three packages with explicit, one-way dependencies:

```
                       ┌────────────────────────────┐
                       │  shared kernel (new)       │
                       │  - schema constants        │
                       │  - model registry          │
                       │  - qdrant client builder   │
                       │  - payload validators      │
                       │  - vector primitives       │
                       └─────────────┬──────────────┘
                                     │ depends on
              ┌──────────────────────┼──────────────────────┐
              │                                             │
       ┌──────▼──────┐                                ┌─────▼─────┐
       │  indexer/   │                                │  search/  │
       │  pipeline   │                                │  API +    │
       │  (library)  │                                │  cache    │
       └─────────────┘                                └───────────┘
              │                                             │
              └──────────► Qdrant (one collection per ◄────┘
                          model version, keyed by _schema_version)
```

**Properties of the target state.**

- The shared kernel has no business logic. Pure utilities, registries, and
  payload definitions.
- `indexer/` has no imports from `search/`. The current
  `from search.qdrant_url import client_kwargs` callsites move to the
  shared kernel.
- `search/` consumes the shared kernel for everything that crosses the
  process boundary: payload schema, model registry, client construction.
- The Qdrant collection per model version is a single source of truth
  for indexed points. Schema version is on every point, not in a side
  table.

### Why this enables the desktop product

The desktop product needs the same pipeline the web backend uses, with:

- a different model entry in the registry (smaller SigLIP-2 variant);
- a different Qdrant collection (or different schema version);
- a different triggering surface (file-system watcher instead of CLI);
- no `search/` package at all on the desktop side.

This plan produces a shape where the desktop app imports only the shared
kernel and the indexer pipeline, not `search/`. Today, it cannot.

---

## 4. Functional changes

Each item below is a **what**. Phasing and ordering are in §5.

### 4.1 Shared kernel extracted

A new package that both `search/` and `indexer/` import. Its scope is
narrow and explicit:

- **Payload schema constants.** The current field names live in
  `indexer/schema.py`; both sides should import from one location.
- **Model registry.** A typed registry mapping model name to (dim,
  resolution, embedder interface, revision-pinning behaviour). No hardcoded
  constants elsewhere.
- **Qdrant client factory.** The current `search/qdrant_url.py` moves here.
  It is what both sides use to build a `QdrantClient`.
- **Payload builders and validators.** `build_payload` and a matching
  `parse_payload` live here, both reading from the schema constants.
- **Vector primitives.** L2 normalize, mean vector, cosine similarity.
  Used by the search side for centroid math and by the indexer for any
  normalization.

The kernel has no I/O of its own and no awareness of HTTP, FastAPI, or
process lifecycle. That constraint is the point: anything that crosses
into business logic does not belong in the kernel.

### 4.2 Schema versioning on every Qdrant point

Every point carries a `_schema_version` payload field, set at index time
and read at search time. Readers refuse points whose version is not in the
known-good set, returning a structured error rather than silently
mis-interpreting the payload.

The version field is what makes a future model swap a backfill operation
instead of a wipe operation. It is also the precondition for the desktop
product to coexist with the web backend in the same Qdrant instance if
that ever becomes a deployment choice — different schema versions on
different collections.

**Version 1 fields.** The first versioned schema adds three fields beyond
what exists today:

- `_schema_version: int = 1` — every point carries this. Set at index
  time. Readers check it on load.
- `folder: str` — absolute parent directory path of the source image.
  Top-level files (image directly in the source root) get
  `folder == source_root`; symmetric with nested files, no special case.
  Powers folder-browsing in the desktop product and folder-grouped
  hydration in the search-side cache (one `GROUP BY folder`, one query
  per folder on demand).
- `model_dim: int` — the vector dimension produced by the embedding
  model that wrote this point. Pairs with the existing `model_name` and
  `model_revision`. Lets a backfilled migration verify each point's
  vector length matches its recorded dim without consulting the model
  registry. Belt-and-braces for the registry ever drifting from
  reality.

`_schema_version` and `model_revision` are independent axes: a point can
keep `_schema_version=1` while `model_revision` changes, or vice versa.
The doc must say so explicitly so a future reader doesn't conflate them.

**Migration from pre-versioned points.** Existing points in deployed
collections lack `_schema_version`. On first read they are treated as
implicit version `0` and migrated to version 1 by the helper below.
The migration is a one-time per-collection operation, not a silent
upgrade; the search side refuses to serve queries against an un-migrated
collection until the operator has run it.

A small migration helper is part of this work: given two collections with
old and new schema versions, produce a third collection of the new
version, applying a registered transform per field. It does not need to
ship in production on day one; it must exist as a script that runs
locally.

**Migration helper interface.** The helper is a callable with a fixed
shape. Defining it here so the implementation has a target, not a
hypothesis:

```python
def migrate_collection(
    *,
    source: QdrantClient,
    target: QdrantClient,
    source_collection: str,
    target_collection: str,
    target_version: int,
    transforms: dict[str, FieldTransform],
    vector_strategy: Literal["copy", "reembed"],
    embedder: Embedder | None = None,
    batch_size: int = 256,
    on_progress: Callable[[MigrationProgress], None] | None = None,
) -> MigrationReport: ...
```

Where:

- `FieldTransform` is a typed callable:
  `(old_payload: dict, model_meta: ModelMeta) -> new_payload: dict`.
  Each field registered for the target version has exactly one transform.
- `vector_strategy="copy"` (default) keeps the existing vector. The
  point's `model_revision` field is updated to indicate the schema
  version changed, but the embedding itself is unchanged. This is the
  schema-only migration.
- `vector_strategy="reembed"` requires an `embedder`; for every source
  point the path is re-loaded and re-embedded. This is a model migration,
  not a schema migration, and is significantly more expensive.
  Documented as a separate operational concern.
- `MigrationReport` carries: total points read, total points written,
  total failures, per-failure detail, elapsed time, and the chosen
  `target_version`.

**Refusal of unknown versions.** The search handler refuses to serve
queries against a collection whose `_schema_version` is not in the
known-good set. The response shape:

- HTTP 503 Service Unavailable (the deployment is not in a usable
  state for that collection).
- Body: `{"error": "schema_version_mismatch", "found": <int>,
  "supported": [<int>, ...], "collection": "<name>"}`. JSON, no
  free-form prose. Stable shape for downstream alerting.
- Logged at ERROR with the same fields. No silent interpretation of an
  unknown payload.

### 4.3 Model registry

A single registry mapping model identity to behavior. The registry is
the only place that knows which models exist; every consumer goes
through it. This is the abstraction you asked for — no model name,
dim, resolution, revision, or embedder call is hardcoded outside the
registry, and the registry is the only thing the indexer and search
sides import for model behavior.

```python
{
  "ViT-gopt-16-SigLIP2-384": ModelSpec(
      dim: int = 1536,
      resolution: int = 384,
      revision: str = "<pinned>",
      text: Embedder,
      vision: Embedder,
  ),
  # Desktop product. Same SigLIP-2 family, smaller than the web backend's
  # gopt variant, but still PyTorch + open_clip — no ONNX runtime, no
  # quantization. Bundle cost: ~600 MB model weights + the PyTorch runtime.
  # Choice rationale: recall on photo search is the primary criterion;
  # bundle size matters but does not outweigh it.
  "ViT-L-16-SigLIP2-256": ModelSpec(
      dim: int = 1024,
      resolution: int = 256,
      revision: str = "<pinned>",
      text: Embedder,
      vision: Embedder,
  ),
}
```

`Embedder` is the single abstraction every model implements:

```python
class Embedder(Protocol):
    """The only model-specific interface the codebase knows about."""

    @property
    def dim(self) -> int: ...
    @property
    def resolution(self) -> int: ...

    def embed_text(self, text: str) -> list[float]: ...
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...
    def embed_image(self, image: Image.Image) -> list[float]: ...
    def embed_images(self, images: Sequence[Image.Image]) -> list[list[float]]: ...
```

**Rules.**

- No file outside `image_search_kernel/registry.py` references a
  model's `dim`, `resolution`, `arch_tag`, `revision`, or call site.
  The regression test in §A3 enforces this.
- Every embedder call site in the codebase goes through
  `registry.get(model_name).text.embed_text(...)` or
  `registry.get(model_name).vision.embed_image(...)`. No
  `VisionEncoder(...)`, no `text_encoder.get_encoder()`, no
  `open_clip.create_model_and_transforms(...)` outside the registry.
- The text encoder and the vision encoder for a given model name are
  registered as a pair. They share `dim`. A future model with separate
  text/vision dimensions would split the pair into two registry entries
  with explicit bridging code; that's a model-registry change, not a
  consumer change.
- Adding a model is a registry entry plus a config flag, nothing else.
  The desktop product's smaller model lands as a registry entry — the
  indexer pipeline consumes it through the same `Embedder` interface
  it consumes the web backend's model.
- Removing a model is a registry deletion plus a check that no
  collection in any environment still references it.
- The text encoder and the vision encoder may be implemented in
  different runtimes (PyTorch for vision, ONNX for text; or vice
  versa). The Protocol hides that. A consumer never imports
  `open_clip` or `transformers` directly — only the registry does.

**Why this enables the desktop product.** The desktop app's indexer
instantiates the same `Embedder` Protocol that the web backend uses,
via a registry entry that may point to a different runtime (e.g.
ONNX Runtime for both encoder towers, downloaded with the app). The
indexer pipeline (§4.4) takes the embedder as a parameter and never
knows or cares which model it is. The pipeline is identical between
products; only the registry entry differs.

### 4.3.1 Embedder implementations and real-model isolation

The registry module is the only place that imports `open_clip`,
`transformers`, `torch`, or any other ML runtime. The rest of the
codebase speaks only `Embedder`.

- **Indexer's vision encoder** (`indexer/vision_encoder.py`) is
  rewritten as a thin wrapper around `Embedder`. It still exists for
  the CLI's `--device` and `--model` flags, but its job is to look up
  the registered embedder and delegate.
- **Search-side text encoder** (`search/text_encoder.py`) is rewritten
  the same way. The mock encoder used in tests becomes a registry
  entry registered under a deterministic name (e.g.
  `"mock-1536"`) so tests don't register it manually.
- **Real-model code lives behind a feature flag** in the kernel's
  registry. A test environment with no GPU registers only the mock;
  the real-model registrations are imported only on platforms where
  `torch.cuda.is_available()` is true (or wherever the real backend
  is configured to run). The kernel package itself is importable on a
  CPU-only host; importing the real-model module triggers the heavy
  imports.
- **Weight downloads are not the registry's job.** The registry
  exposes a `download_weights()` hook per spec; the actual download
  is delegated to a downloader that's also registered. The first
  call to `embed_image` triggers a lazy download with a typed error
  if it fails (offline, missing credentials, etc.).

This is the abstraction. The plan does not say *which* model the
desktop product uses — it pins the abstraction, the registry, and
the rules. The concrete desktop model entry (`ViT-L-16-SigLIP2-256`)
is decided out-of-band and lands as a registry entry when the desktop
product's first PR opens.

### 4.4 Indexer pipeline as a library

The current `indexer/local_sync.py` is a CLI that glues together the
modules. The glue becomes a first-class object: an `IndexerPipeline` with
explicit phases — scan, load, embed, upsert — and an interface for each.

The desktop app's Electron main process will instantiate this same
pipeline (potentially with a different embedder implementation, e.g.
ONNX Runtime instead of open_clip + PyTorch). The CLI is one consumer;
the desktop app is another.

The pipeline supports:

- **Streaming batched outputs.** Memory-bounded; works on libraries of any
  size.
- **Cancellation.** Long-running scans stop cleanly on user request
  (important for the desktop app where the user can quit mid-sync).
- **Progress reporting.** A typed progress event with phase, count,
  rate, ETA.
- **Dry-run mode.** Already exists in the CLI; promoted to a pipeline
  flag.
- **Idempotent re-runs.** Already works via deterministic ids; verified
  as a property of the pipeline, not a side effect of the CLI.

**Pipeline contract.** The phases form a pull-based pipeline. Each
phase is an iterator that pulls from the previous phase's output. The
driver is a single sync loop; per-phase concurrency is configured
independently. The shape:

```python
class ScanPhase(Protocol):
    def __call__(self, source: Path) -> Iterator[Path]: ...

class LoadPhase(Protocol):
    def __call__(
        self, paths: Iterator[Path], *,
        on_failure: Callable[[Path, LoaderError], None],
    ) -> Iterator[tuple[Path, Tensor]]: ...

class EmbedPhase(Protocol):
    def __call__(
        self, items: Iterator[tuple[Path, Tensor]], *,
        embedder: Embedder,
    ) -> Iterator[tuple[Path, Tensor, Vector]]: ...

class UpsertPhase(Protocol):
    def __call__(
        self, items: Iterator[tuple[Path, Tensor, Vector]], *,
        client: QdrantClient, collection: str,
        dry_run: bool, batch_size: int,
    ) -> Iterator[WriteResult]: ...
```

Properties pinned by this contract:

- **Sync at the phase boundary.** Each phase is a sync iterator. The
  desktop app wraps the full pipeline in an async boundary (a worker
  thread or a child process) — the pipeline itself doesn't need to be
  async, and forcing it would complicate every phase implementation.
- **Pull, not push.** Each phase decides when to ask for the next
  item. This is what makes cancellation work: dropping the iterator
  drops the whole pipeline cleanly.
- **Per-phase concurrency is configured, not global.** Scan is
  sequential (filesystem-bound, no parallelism helps). Load uses a
  `ThreadPoolExecutor` for I/O (PIL decode). Embed is single-threaded
  per pipeline instance (a model forward is GIL-bound); the desktop
  app runs multiple pipeline instances if it wants model-parallel.
  Upsert is sequential (Qdrant ordering).
- **Failure is reported, not raised mid-stream.** Each phase takes an
  `on_failure` callback. The driver aggregates failures into a
  `PipelineReport` at the end. The pipeline completes; the CLI exits
  non-zero with the report.
- **Progress is a typed event stream**, not a callback. The driver
  emits `ProgressEvent(phase, count, rate, eta_seconds)` at configured
  boundaries. The CLI prints; the desktop app renders a progress bar.

**Cancellation.** A `threading.Event` (or `asyncio.Event` in the async
wrapper) is passed to the driver. The driver checks it between phases
and between batches within `upsert`. On cancellation, in-flight
iterators are closed; partial upserts are not committed. Final state:
no Qdrant changes from the cancelled run.

### 4.5 API layer reorganization

The 39 routes in `search/app.py` become one module per resource group,
each self-contained: routes, request/response models, dependencies, and
any helpers used only by that resource. Shared concerns (auth middleware,
lifespan, exception handlers, static mounts) live at the top level and
are imported into each module — not duplicated.

What changes for callers:

- A new resource is a new module. No existing module needs to be edited.
- A route change is contained to one file.
- OpenAPI stability is enforced by a test that diffs the generated spec
  against a checked-in fixture; that test stays and is extended to cover
  every router module.

What does not change:

- The URL paths, response shapes, and status codes. These are part of
  the public contract.
- The auth model (single-user signed cookie).
- The middleware ordering.

### 4.6 Search internals: pure compute vs. IO

Today, `centroids.py`, `diversity.py`, `for_you.py`, and `discover.py`
each mix computation with persistence. After this work:

- A **compute module** per algorithm: pure functions taking typed inputs,
  returning typed outputs, with no Qdrant or filesystem calls.
- A **persistence module** per algorithm: the on-disk file format and
  IO. Reads the compute module's outputs and persists them.
- A **service module** per algorithm: orchestrates the two, owns any
  caching, and is the only thing the API layer imports.

This is the same shape as the indexer pipeline (§4.4) and the shared
kernel (§4.1): a single discipline applied throughout.

**Per-algorithm classification.** The four algorithms split
differently. Pinning the classification here so the B3 refactor has a
target:

| Module | Compute purity | Inputs | Persistence | Service responsibility |
|---|---|---|---|---|
| `centroids` | Pure | Vectors (from Qdrant), `k` | Centroid file (atomic JSON or SQLite) | Orchestrates: scroll Qdrant → compute → persist; serves search-side reads |
| `diversity` | Pure | Candidates (from Qdrant), `mode` | Settings only (relevance drop per mode) | Reads candidates from Qdrant, calls compute, applies weights |
| `for_you` | Parameterizable | User state (favorites, dislikes) + Qdrant recommend | User-state cache (TTL'd) | Reads user state + Qdrant → recommends → ranks |
| `discover` | Stateful | Prior picks + Qdrant recommend | State machine (pick history) | Manages pick sequence; orchestrates Qdrant + state |

The split per algorithm is `(compute + persistence + service)`. Where
the algorithm is "pure," the compute module has no I/O and is trivially
unit-testable. Where it's "parameterizable," the compute module takes
its non-vector inputs as parameters; the service module is what wires
those from the live user state. Where it's "stateful," persistence is
non-trivial — the service module owns the state machine, not just the
caching.

### 4.7 Cache subsystems

Two caches exist:

- **Search-side index cache.** Today: synchronous SQLite.
- **Indexer-side "what's indexed" cache.** Today: hand-rolled JSON.

Both must:

- be crash-safe (a process kill mid-write does not corrupt the file);
- write atomically (a reader never sees a partial state);
- carry a version field that the loader checks (a stale format is
  rejected, not interpreted);
- expose an integrity check that detects drift between the cache and
  its source of truth;
- support lazy/background refresh where the source of truth changes
  asynchronously (this matters for the search cache, which scrolls
  Qdrant on startup today).

Both end up on the same durable substrate (SQLite) with the same shape
(versioned, atomic, integrity-checked). The persistence concerns become
identical; only the contents differ.

### 4.8 Async pipeline

All blocking I/O runs off the event loop. The single rule:

> A handler may not block on I/O, period.

Concrete consequences:

- Search-side synchronous SQLite is replaced with an async driver. The
  handler awaits it; no `to_thread` wrappers needed.
- Qdrant calls go through the async client. Multiple in-flight requests
  multiplex on a single HTTP/2 connection.
- Any remaining synchronous third-party call gets an async wrapper or
  is replaced.
- CPU-bound work that has parallel potential (PIL decode, blurhash) uses
  process- or thread-pool concurrency with explicit batch boundaries.

**Per-subsystem concurrency choice.** Pinned here so the implementation
doesn't have to rediscover the tradeoff:

| Subsystem | Mechanism | Rationale |
|---|---|---|
| Request handler (FastAPI route) | async / await | Required: handlers must not block the event loop |
| Qdrant I/O | `AsyncQdrantClient` (httpx under the hood) | Multiplexes requests on a single HTTP/2 connection; matches §4.14 |
| Search-side SQLite | `aiosqlite` | Real async driver; replaces per-call `asyncio.to_thread` |
| Indexer — scan phase | Sequential | Filesystem-bound; parallelism doesn't help; reordering breaks the snapshot invariant |
| Indexer — load phase | `ThreadPoolExecutor` | PIL decode is GIL-released during C calls; threads win |
| Indexer — embed phase | Single-threaded per pipeline | Model forward is GIL-bound and GPU-bound; the gain from parallelism is zero unless running multiple model instances |
| Indexer — upsert phase | Sequential | Qdrant ordering; one batched call per `batch_size` items |
| Text encoder (search side, post-C4) | Lazy load + read-only after init | One instance per process; thread-safe reads |
| Model registry | Read-only after init | Concurrent reads safe; init guarded by a `threading.Lock` |
| Migration helper | Single-threaded | Long-running but not in the request path; CLI-only |

**Pool sizing.** Defaults, all env-configurable:

- Indexer load pool: `min(cpu_count, 8)`, clamped to `[1, 32]`.
- Indexer embed pool: `1` per pipeline instance. Multiple instances
  supported for desktop app model-parallelism.
- Async Qdrant client connection pool: `min(uvicorn_workers * 4, 32)`.
- Thread pool for legacy `asyncio.to_thread` (transitional): default
  40, never larger than the model's worker count.

The async migration's concrete call-site changes are documented inline in
each phase's implementation PR.

### 4.9 Performance budget

After this work, the backend has measurable budgets per dimension.
Each is enforced by a benchmark that fails the build if exceeded.

| Dimension | Budget |
|---|---|
| Indexing throughput (1k synthetic images, mock encoder, in-memory Qdrant) | ≥ 200 img/s on a 4-core CI host |
| Query latency p95 (warm cache, in-memory Qdrant, mock encoder) | < 50 ms |
| Query latency p95 (warm cache, networked Qdrant on LAN) | < 200 ms |
| App cold start (post-env-load, model NOT loaded) | < 5 s |
| App cold start (post-env-load, including model load from local cache) | < 60 s |
| Resident memory, search container (idle, model loaded) | < 4 GB |
| Resident memory, indexer (after 10k synthetic images indexed) | < 6 GB |
| Test coverage on critical modules (upsert, image_loader, cache, vision_encoder, schema) | ≥ 80% |

These are starting points. They get tightened as the implementation
hits them. **Absolute numbers** are used where possible; the one
relative target (≥ 2× current for indexing throughput in §C2) is
justified by the concurrency model — it must be re-measured as an
absolute number once C2 lands, then the table is updated.

**Measurement conditions.** Each budget above is enforceable only
under specified conditions. Pinning them here so a benchmark written
in one environment produces comparable numbers in another:

- **Host class.** The benchmark host is a Linux container or VM with
  at least 4 vCPU and 8 GB RAM. CI runners below this are flagged
  in the benchmark output but don't fail the build; CI runners at
  or above this enforce the budgets.
- **Encoder.** All benchmarks use the mock encoder by default
  (deterministic, GPU-free). Real-model benchmarks are gated behind
  `BENCH_REAL_MODEL=1` and are not in the default CI run. A
  real-model benchmark run is a manual operation; its results are
  tracked but not gating.
- **Qdrant.** In-memory Qdrant by default. Networked-Qdrant
  benchmarks use a sidecar `qdrant/qdrant:v1.13+` container (the
  latest stable, not the pinned `v1.7.4`); the budget numbers above
  assume < 1 ms RTT to the Qdrant process.
- **Fixtures.** Indexing throughput uses 1000 synthetic 64×64–256×256
  JPEGs generated from a fixed seed. Query latency uses 1000
  randomly-positioned points in a fixed-dim vector space with
  unit-norm L2-normalized random vectors. These fixtures live in
  `benchmarks/fixtures/` and are checked in.
- **Warm-up.** Each benchmark runs a 5-second warm-up before the
  measurement window. The first measurement is discarded.
- **Iterations.** Query-latency benchmarks run ≥ 200 iterations and
  report p50/p95/p99. Indexing-throughput benchmarks run ≥ 3 full
  passes and report the median.
- **Noise margin.** A regression fails the build only if the
  measured value exceeds the budget by more than 10% (configurable
  via `BENCH_NOISE_MARGIN`). This absorbs CI jitter without
  hiding real regressions.

### 4.10 Observability baseline

A response-time middleware logs duration and status per request. RSS
sampling happens at startup and at a fixed interval (configurable). Logs
are structured (JSON) so a downstream dashboard can graph them.

This is not a feature for the user; it is the precondition for the next
set of optimizations to be measurable.

### 4.11 Backwards compatibility

Four constraints that must hold through every phase. Each has a CI
gate or a contract test that enforces it.

1. **On-disk Qdrant collections remain readable.** Existing points
   without a `_schema_version` field are read as implicit version `0`
   and refuse to serve queries until the operator has run the migration
   helper (§4.2) to produce a version-1 collection. The refusal
   response is documented in §4.2.1 (HTTP 503 with a stable JSON body).
   No silent interpretation of an unknown payload shape.
   *Test:* `tests/test_schema_version_refusal.py` asserts the response
   shape, status code, log level, and that no Qdrant call returns
   data before the refusal.
2. **`local_sync` CLI flags continue to work.** New flags are added;
   no existing flag changes meaning or is removed. The indexer
   continues to be the right place for one-time backfills of an
   existing collection.
   *Test:* `tests/test_cli_flags_contract.py` parses
   `local_sync --help`, snapshots the flag set, and fails if a flag
   disappears or changes default in a non-additive way.
3. **API contract.** The OpenAPI spec is checked in. A diff against it
   fails the build. New fields are additive; old fields are never
   removed.
   *Test:* the existing `tests/test_openapi_stability.py`.
4. **No silent interpretation of unknown payloads.** A point with an
   unrecognized `_schema_version` is treated as refused (per #1),
   not coerced to a known version. A point missing a required field
   for its declared version is refused at parse time.
   *Test:* `tests/test_schema_version_refusal.py` includes a case
   for "version present but missing a required field."

### 4.12 Test strategy

Testing is a mandatory part of every change in this plan. No PR merges
without tests that prove the change works and that the bug pattern the
change is fixing cannot silently return.

**Test pyramid.** Four levels, each with its own gate:

1. **Unit tests** — every module in the shared kernel, every algorithm
   compute module, every utility function. No I/O. Fast: < 50 ms each,
   full unit suite < 30 s. Run on every commit.
2. **Integration tests** — full-pipeline tests with in-memory Qdrant
   (no network, no Docker). Cover: indexer pipeline end-to-end, search
   handler against an indexed collection, schema version migration
   round-trip. Run on every commit.
3. **Contract tests** — OpenAPI spec doesn't drift (already exists as
   `tests/test_openapi_stability.py`); payload schema doesn't drift;
   `_schema_version` validation rejects unknown versions; CLI flags
   don't disappear. Run on every commit.
4. **Performance tests** — the benchmarks from §4.9 enforce the budgets.
   Run on every PR; a regression fails the build.

**Per-change requirements.** Every change in §4 ships with:

- **Unit tests for the new module / function.** Red-green-refactor: the
  test is written first, the change makes it pass, the implementation
  is cleaned up. The existing `tests/` suite style (pytest, in-memory
  Qdrant fixtures, mock encoder) is the model.
- **Integration test for the wired-up behavior.** Not just "the unit
  passes" but "the unit works when called from the place that actually
  calls it."
- **Regression test for the original bug pattern.** Concretely: a test
  that fails if the pattern the change is meant to prevent reappears.
  Examples: a grep-based test that fails if `VECTOR_DIM = 1536` appears
  outside the registry module; a test that fails if any point payload
  lacks `_schema_version`; a test that fails if `indexer/` imports from
  `search/`.
- **Contract test if the change touches the public surface.** OpenAPI
  drift, payload schema drift, CLI flag drift.
- **Benchmark updated if the change is perf-relevant.** A perf change
  ships with a benchmark that demonstrates the improvement, not a
  comment that it does.

**Coverage gates.**

- Critical modules (`upsert`, `image_loader`, `cache`,
  `vision_encoder`, shared kernel's `schema`, `registry`,
  `payload`) — ≥ 80% line coverage, enforced.
- All other modules — best effort, tracked but not enforced.
- A test coverage drop on any tracked file fails the build.

**Test discipline.**

- TDD where the bug is clear (red-green-refactor).
- Tests stay in lockstep with the code they cover: when a refactor
  invalidates a test's premise, the test is rewritten before the PR
  merges. Stale tests that pass for the wrong reason are a regression
  risk; they fail code review.
- Tests are deterministic. No real network, no real time, no real
  filesystem beyond `tmp_path`. The in-memory Qdrant + mock encoder
  pattern from the existing suite is the model.
- Flaky tests block their PR. No `pytest.mark.flaky`, no `xfail`,
  no `try/except` to silence.

### 4.13 Engineering standards

The refactor extends the project's existing conventions; it does not
reinvent them. The baseline today:

- **Python 3.10+** (per `pyproject.toml` target).
- **ruff** with line-length 100, rules `F + E/W + I + S + C4 + SIM +
  B + UP` (security, comprehensions, simplifications, bugbear,
  pyupgrade). Per-file ignores exist for `tests/`, `bin/`, vendored
  artifacts.
- **pytest** with `asyncio_mode = "auto"`.

What the refactor adds:

**Type discipline.**

- `mypy --strict` is the gate for the shared kernel (Phase A1). Every
  public symbol exported from the kernel is fully typed: no `Any`,
  no implicit `Optional`, no `cast` without a comment.
- `mypy --strict` is the gate for the model registry, payload builders,
  payload validators, and the migration helper. These are the modules
  where the bug patterns the audit found (hardcoded dims, mismatched
  field names) are most likely to recur.
- `mypy` (default settings, not strict) is the gate for the rest of
  the codebase. Existing legacy modules are not blocked on `strict`,
  but new modules in `search/` and `indexer/` from Phase B onward are.
- A `mypy` regression on any tracked module fails the build.

**Lint and format.**

- Existing ruff config stays. New rule categories added by the refactor
  (e.g. `TCH` for type-check blocks, `RUF100` for unused `# noqa`) are
  added with rationale, not silently.
- No project-specific formatters (no Black, no isort-as-tool). ruff
  handles both.

**Logging conventions.**

- Logs are structured (JSON) at the `search/` and `indexer/` layer
  boundary; library code uses stdlib `logging` with named loggers.
- Every emitted log line carries: timestamp (ISO-8601 UTC), level,
  logger name, message, and a recognized set of contextual fields
  (`request_id`, `point_id`, `phase`, `model_name`).
- New modules do not call `print`. `bin/` scripts are exempt (per
  existing ruff config).
- Log level is configurable; defaults to INFO in production, DEBUG
  in tests.

**Error handling.**

- Library code raises typed exceptions (`LoaderError`, `MigrationError`,
  `RegistryMissError`, etc.). The kernel defines the exception
  hierarchy; downstream modules do not invent ad-hoc base classes.
- Route handlers translate typed exceptions to HTTP responses via a
  central exception handler map. No `try/except Exception: pass` on
  the request path.
- The indexer pipeline surfaces errors as typed results, not silent
  skips. A failed image is a `PipelineFailure` with the path, the
  phase, and the cause. Aggregate failures are reported at the end;
  the CLI exits non-zero if any image failed.
- Silent recovery (a failed PIL decode returns a placeholder) is
  documented and only allowed where the indexer already does it
  today, with an explicit `# allow-silent-skip` comment.

**Dependency policy.**

- New runtime deps require a justification in the PR description:
  what does it replace, what's the maintenance signal, what's the
  footprint.
- Dev deps (`pytest`, `httpx`, `ruff`, etc.) follow the existing
  pattern in `[project.optional-dependencies].dev`.
- The split between `search` and `indexer` deps, flagged in the audit,
  becomes a `pyproject` extras split: `pip install .[search]` for
  the API server, `.[indexer]` for the indexer, `.` for both.
  Verify the existing single-container image still builds with both
  installed together; verify the search-only image runs without
  the indexer extras.
- Deps are version-pinned at the lower bound (`>=X.Y`) and capped by
  the lockfile. No unpinned deps land.

**ADRs for major decisions.**

Each of the following produces an ADR in `docs/adr/`:

- A1 — why a shared kernel, what shape it takes, what doesn't belong.
- A3 — why a registry vs. config; what the registry does and doesn't
  enforce.
- A2 — why schema versioning now; what the migration helper's contract is.
- B1 — why a pipeline class with explicit phases; what alternate shapes
  were considered.
- B5 — why lazy refresh; what the staleness window is.
- C1 — why `AsyncQdrantClient` over `httpx` directly; what tradeoffs.

ADRs are short (one page each), written before the implementation lands,
and reviewed with the implementation PR. A future refactor that
contradicts an ADR updates the ADR first.

**Deprecation policy.**

- Symbols removed by a refactor (e.g. `search.app.create_app`'s
  factory internals as it becomes a thin assembly) get a
  `DeprecationWarning` for one minor version, then are removed.
- Public route URLs are never removed in the lifetime of this plan
  (per §4.11). Internal symbols may be.
- The CLI's `--flag` removal requires a migration note in the CHANGELOG.

**Public API vs. internal.**

- `search/`, `indexer/`, and the shared kernel each declare an `__all__`
  listing public symbols.
- Underscore-prefixed names are internal by convention; they may
  change without notice.
- A test imports from `__all__` only. An import from a non-`__all__`
  symbol fails code review.

### 4.14 Concurrency safety

The plan introduces concurrency at multiple points. Each subsystem
states its concurrency contract so the next reader doesn't have to
guess.

| Subsystem | Thread-safe? | Process-safe? | Notes |
|---|---|---|---|
| Shared kernel (payload builders, registry, schema) | Required | Required | Pure functions; trivially safe |
| Indexer pipeline phases | Each phase is independent | Per-phase choice | Cancellation must propagate |
| Indexer pipeline driver | Single-threaded by default | Multi-process optional | C2 picks this explicitly |
| Search-side index cache (SQLite) | Required | Required | `aiosqlite` or `check_same_thread=False` |
| Model registry (loaded at startup) | Required (read-only after init) | Required | Read-only after init; concurrent reads safe |
| Qdrant client | Per-call thread-safe | Per-call thread-safe | Per `qdrant-client` docs |
| Text encoder (loaded model) | Required after C4 | Out of scope | Single-process model; multi-process via uvicorn workers is the deployment's problem |

**Cancellation.**

- Long-running request handlers (`/api/for-you/feed`, `/api/discover/*`,
  `/api/similar/{id}?limit=large`) accept a request cancel and abort
  their work in < 100 ms. Verified by an integration test that
  cancels mid-flight and asserts cleanup.
- The indexer pipeline responds to a cancellation event within one
  batch boundary, releasing resources and flushing state.

**Progress reporting.**

- The indexer pipeline emits typed progress events (`phase`, `count`,
  `rate`, `eta_seconds`) at configurable boundaries. The desktop app's
  UI consumes the same event shape the CLI consumes today.

**Resource limits.**

- Concurrent PIL decode (C2) defaults to `min(cpu_count, 8)` workers.
  Configurable via env var; tests pin it to a deterministic value.
- The async Qdrant client (C1) has an explicit connection-pool cap.
  No unbounded connection growth.

---

## 5. Phasing and ordering

Each phase produces a working backend. No phase lands a partial state
that breaks tests or the deployed service.

### Phase A — Foundations

The items that block everything else. They produce no visible perf wins
yet; they make the rest possible.

**A1. Shared kernel extracted** (§4.1). New package, move
`qdrant_url.py` and `schema.py` and the model dim constant in. Both
`search/` and `indexer/` import from it. No behavior change.

- *Tests:*
  - **Unit:** every public symbol in the kernel has tests covering
    the happy path and the documented failure modes. Payload
    round-trip: build → parse returns the same data.
  - **Integration:** existing tests (`tests/test_search_api.py`,
    `tests/test_local_sync.py`) keep passing without modification —
    they exercise the kernel through the public APIs.
  - **Regression:** a grep-based test fails if any code outside the
    shared kernel imports `qdrant-client` URL helpers or payload
    constants directly. The shared kernel is the only path.
  - **Type gate:** `mypy --strict` passes on the kernel package.
  - **Contract:** the existing `tests/test_schema.py` field-name
    cross-check still passes (and is updated to look at the new
    location).

**A2. Schema versioning on Qdrant points** (§4.2, §4.11). Every
existing write site sets `_schema_version`. Every existing read site
checks it. Migration script produces a backfilled collection from an
old one.

- *Tests:*
  - **Unit (kernel):** `_schema_version` is present in every payload
    produced by `build_payload`. `parse_payload` rejects unknown
    versions with a typed exception. `parse_payload` accepts
    version 1 with `folder` and `model_dim` fields. `parse_payload`
    rejects a version-1 payload that's missing `folder`.
  - **Unit (migration helper):** given a synthetic v0 input payload,
    the helper produces an equivalent v1 output payload with
    `folder = path.parent`, `model_dim = <registry dim>`,
    `_schema_version = 1`. Edge cases: top-level file (folder ==
    root), non-existent path (typed error), corrupt JSON.
  - **Integration:** in-memory Qdrant, write a v1 point, read it
    back, assert every field is intact. Write a v0-style point
    (no `_schema_version`), read it, assert the read path refuses
    to serve until migration runs.
  - **Integration:** full migration run: fixture v0 collection →
    helper produces v1 collection → every input point has a v1
    counterpart with the right fields.
  - **Regression:** a test fails if `build_payload` is called without
    setting `_schema_version`. A test fails if the read path accepts
    a payload whose `_schema_version` is not in the known-good set.
  - **Contract:** `SCHEMA.md` lists `_schema_version`, `folder`,
    `model_dim` along with the existing fields; the existing
    field-name cross-check test passes.
  - **Coverage:** kernel module ≥ 80%.

**A3. Model registry** (§4.3, §4.3.1). Registry in the shared kernel.
The three hardcoded dim/resolution/tag constants in `indexer/upsert.py`,
`indexer/image_loader.py`, and `search/config.py` are replaced with
registry lookups. The `Embedder` Protocol is the only model-specific
interface the codebase knows about.

- *Tests:*
  - **Unit (registry):** add/lookup raises typed exception on miss.
    `dim`, `resolution`, `embedder` come from the registered entry,
    not from any module-level constant.
  - **Unit (Embedder Protocol):** a mock implementation of the
    Protocol passes a structural type check (`isinstance` against
    `runtime_checkable`). Both `embed_text` and `embed_image` return
    unit-norm vectors of the right dim.
  - **Unit (mock encoder):** the test mock encoder is registered
    under a deterministic name (e.g. `"mock-1536"`) by the test
    fixture, so individual tests don't register it manually. Test
    fixture teardown removes it.
  - **Integration (indexer):** the indexer creates a Qdrant
    collection with `dim` from the registry. The vision encoder is
    looked up by name. Both fail loudly (typed exception, not
    silent fallback) if the model name has no registry entry.
  - **Integration (search):** the text encoder is looked up by name
    on first request (post-C4). Same loud-fail behavior.
  - **Integration (kernel importability):** the kernel package
    imports successfully on a CPU-only host with no `torch` /
    `open_clip` / `transformers` installed. The real-model
    registration module is conditionally imported based on the
    configured runtime.
  - **Regression:** a grep-based test fails if `VECTOR_DIM`,
    `_EMBED_DIM`, `1536`, `384`, `open_clip`, `transformers`,
    `torch` appear in `indexer/` or `search/` outside the
    registry-module whitelist. (Test fixtures and the registry
    module itself are allowed.)
  - **Regression:** a test fails if any consumer imports
    `VisionEncoder` directly, `text_encoder.get_encoder` directly,
    or `open_clip.create_model_and_transforms` directly. All embedder
    calls go through `registry.get(...).text` or
    `registry.get(...).vision`.
  - **Type gate:** `mypy --strict` passes on the registry module
    and on the `Embedder` Protocol declaration.
  - **Coverage:** registry module ≥ 80%.

**A4. Indexer critical-module unit tests** (§4.9, coverage row). Tests
for `upsert` (id stability, payload assembly), `image_loader`
(corrupt input, EXIF rotation, normalize), `cache` (atomicity, drift
detection, version mismatch), `vision_encoder` (mock embed round-trip,
dim correctness). These tests stay green through every subsequent
phase.

- *Tests:*
  - **Unit (upsert):** `id_for` is deterministic across runs (same
    path → same id). `id_for` differs across shards. `build_payload`
    populates every declared field. Payload missing a declared
    field fails the test.
  - **Unit (image_loader):** corrupt JPEG raises `LoaderError`. PNG
    with EXIF orientation is transposed. Empty file raises
    `LoaderError`. Non-image extension raises `LoaderError`. Output
    is `CHW float` with the right shape.
  - **Unit (cache):** write-then-read round-trip. Two concurrent
    writers don't corrupt the file (lock semantics). Killing mid-write
    leaves the file readable (atomic rename). Stale `CACHE_VERSION`
    triggers a typed error, not silent re-load.
  - **Unit (vision_encoder):** mock embed returns a 1536-dim vector,
    L2-normalized. Different seeds → different vectors. Same seed →
    same vector.
  - **Coverage gate:** the four modules plus `indexer/schema.py` hit
    ≥ 80% line coverage; CI fails below.
  - **Regression:** these tests are the canary for every Phase B
    change. A refactor that breaks them is the wrong refactor.

### Phase B — Modularization

Refactors that improve organization without changing behavior. Done
after Phase A so the tests in A4 catch any regression.

**B1. Indexer pipeline as a library** (§4.4). `IndexerPipeline` class
with explicit phase interfaces. `local_sync` CLI calls into it. The
class is importable by the future desktop product without dragging
in `search/`.

- *Tests:*
  - **Unit (per phase):** each phase interface has its own test
    class with a fake downstream. The `scan` phase returns a
    deterministic list from a `tmp_path`. The `load` phase produces
    a tensor of the right shape. The `embed` phase invokes a fake
    embedder with the right input and returns its output. The
    `upsert` phase batches and writes idempotently.
  - **Unit (cancellation):** start the pipeline, signal cancellation
    mid-batch, assert the pipeline releases within one batch boundary
    and emits a `PipelineFailure` with `phase='cancelled'`.
  - **Unit (dry-run):** dry-run produces a `PipelineReport` with the
    intended writes but performs no Qdrant calls. Verifiable with a
    spy on the Qdrant client.
  - **Unit (idempotency):** re-running on the same `tmp_path` produces
    the same point ids; Qdrant upserts are no-ops.
  - **Unit (progress):** the pipeline emits `ProgressEvent`s at the
    configured boundaries (every N images, every phase transition).
    Tests assert shape and ordering.
  - **Integration:** full pipeline run against an in-memory Qdrant.
    Output matches the same input run through the existing
    `local_sync` flow (point count, ids, payload shape).
  - **Regression:** a test fails if `indexer/` imports anything from
    `search/`. The dep direction from §3 is enforced.
  - **Coverage:** `indexer/pipeline.py` (the new module) ≥ 80%.
  - **Backward-compat:** the existing `tests/test_local_sync*.py`
    suite keeps passing without modification — `local_sync` is now
    a thin wrapper over the pipeline.

**B2. API layer reorganization** (§4.5). One router module per resource
group. The factory in `app.py` becomes a thin assembly. OpenAPI
stability test extended.

- *Tests:*
  - **Contract:** `tests/test_openapi_stability.py` extends to
    diff the generated spec against the checked-in fixture, with
    one entry per router module. Any drift fails the build.
  - **Unit (per router):** each new router module ships with focused
    unit tests. No router has fewer tests than routes it exposes.
  - **Integration:** the full existing `tests/test_*_api.py` suite
    keeps passing without modification. Same URLs, same response
    shapes, same status codes.
  - **Regression:** a test fails if `app.py` (or any single file)
    exceeds the line-count threshold (e.g. 500 lines). A real
    professional monorepo flags monolith growth before it happens.
  - **Backward-compat:** no URL or response field changes. New
    fields are additive.

**B3. Search internals: compute vs. IO separation** (§4.6). Each of
`centroids`, `diversity`, `for_you`, `discover` becomes (compute +
persistence + service). No call site outside the service imports the
compute module directly.

- *Tests:*
  - **Unit (compute):** each compute module is pure — tests inject
    inputs, assert outputs, and assert no Qdrant / filesystem /
    network calls happened (mock or spy).
  - **Unit (persistence):** each persistence module has a test for
    the on-disk format and the IO behavior. Round-trip: write →
    read → equal.
  - **Unit (service):** the service module orchestrates compute and
    persistence correctly under fake inputs.
  - **Integration:** existing `tests/test_centroids.py`,
    `tests/test_diversity.py`, `tests/test_for_you.py`,
    `tests/test_discover.py` keep passing.
  - **Regression:** a test fails if any code outside a service
    module imports a compute module directly.
  - **Coverage:** every new compute module ≥ 80%.

**B4. Indexer `cache` on SQLite** (§4.7). Replace JSON with SQLite.
Same versioning + atomicity semantics. Existing data is migrated by the
script that ships with A2.

- *Tests:*
  - **Unit:** SQLite cache round-trip preserves entries. Concurrent
    writers don't corrupt (lock semantics). Killing mid-write leaves
    the DB readable (transaction semantics). Stale `CACHE_VERSION`
    raises typed error, not silent re-load.
  - **Unit (migration):** an existing JSON cache from B4's fixture
    set migrates to SQLite byte-for-byte equivalent in observable
    behavior (lookups return the same answers).
  - **Integration:** the existing `tests/test_local_sync*.py` suite
    keeps passing. Caching behavior is identical from the indexer's
    perspective.
  - **Regression:** a test fails if any code in `indexer/` writes
    to a `.json` cache file. The JSON format is no longer supported.
  - **Coverage:** `indexer/cache.py` ≥ 80%.

**B5. Search-side index cache async + lazy refresh** (§4.7, §4.8).
Async driver, lazy startup. The app serves stale cache while a
background task refreshes.

- *Tests:*
  - **Unit (async):** every `index_db` method is `async def`; the
    test asserts awaiting is required (the test calls without
    `await` and fails to compile — or, for runtime, the test asserts
    the return is a coroutine).
  - **Unit (lazy refresh):** startup completes without hydrating
    the cache from Qdrant. The first read triggers a hydrate.
  - **Integration:** the app serves a request before hydration
    completes (returns from stale cache, then re-reads once hydrate
    finishes).
  - **Integration:** during a background refresh, a concurrent
    read sees consistent data (no torn rows).
  - **Regression:** the existing `tests/test_index_db.py` keeps
    passing after the methods are made async.
  - **Performance:** cold-start benchmark (§C7) shows the reduction.
  - **Coverage:** `search/index_db.py` ≥ 80%.

### Phase C — Performance

The actual perf wins. Each item produces a measurable change on the
budget in §4.9.

**C1. Async pipeline end-to-end** (§4.8). Async Qdrant client. All
blocking I/O off the event loop. This alone is the largest single
perf item.

- *Tests:*
  - **Contract:** all existing API tests pass. Same responses, same
    latency distribution within budget.
  - **Integration:** a request is no longer blocked on another
    request's Qdrant call. Verified by a test that fires two slow
    requests in parallel and asserts neither waits for the other.
  - **Regression:** no `asyncio.to_thread` wrapper on a search-side
    hot path. A grep-based test fails if one re-appears.
  - **Performance:** query-latency benchmark within the §4.9 budget.

**C2. Concurrent PIL decode** (§4.8). The indexer's image-load phase
runs with a configurable worker pool. Throughput doubles at minimum
on a non-GPU-bound host.

- *Tests:*
  - **Unit:** the worker-pool configuration is read from env, defaults
    to `min(cpu_count, 8)`, and is clamped to `[1, 32]`.
  - **Integration:** the indexer processes N synthetic images with
    worker pool ≥ 2 in less than 50% of the single-worker time, on a
    CI-sized corpus.
  - **Regression:** a test fails if the indexer is single-threaded by
    default for I/O-bound phases.
  - **Performance:** indexing-throughput benchmark ≥ 2× the A4 baseline.

**C3. Precompute blurhash at index time** (§4.9). Move blurhash from
search-time to index-time. Removes per-request work on the hot path.

- *Tests:*
  - **Unit:** the indexer computes blurhash during the load phase.
    The search response still has a `blurhash` field.
  - **Integration:** an indexed image has `blurhash` set in its
    payload. A search response returns the same `blurhash` value.
  - **Regression:** no code path computes blurhash on the request
    side. A grep-based test fails if a blurhash import lands in
    `search/`.
  - **Performance:** search-latency benchmark within the §4.9 budget.
    The per-request blurhash compute is gone.

**C4. Lazy model load** (§4.9). The text encoder is loaded on first
request, not at startup. Cold-start budget drops from minutes to
seconds.

- *Tests:*
  - **Integration:** `create_app()` completes without loading the
    SigLIP2 model. The first `/api/search` request triggers the
    load. Subsequent requests use the cached encoder.
  - **Integration:** concurrent first-requests don't load the model
    twice (a lock guards the load).
  - **Regression:** a test fails if the text encoder is imported
    at module top level (the pattern that triggered eager loading).
  - **Performance:** cold-start benchmark < 5 s.

**C5. Static asset cache headers** (§4.9). Browser-side caching on
hashed frontend assets and resized photo variants.

- *Tests:*
  - **Contract:** `GET /_app/immutable/*` returns
    `Cache-Control: max-age=31536000, immutable`. The OpenAPI spec
    does not change; the test asserts the response header directly.
  - **Contract:** `GET /photo/{id}/raw` returns
    `Cache-Control: must-revalidate` (the variant case).
  - **Performance:** a synthetic load test of 100 photo-tile fetches
    shows cache reuse across the second-and-later fetches.

**C6. Response-time middleware + structured logs** (§4.10). Baseline
observability. Numbers feed into the benchmark dashboard.

- *Tests:*
  - **Unit:** the middleware emits a structured log line per request
    with the fields named in §4.13. Field set is fixed and tested.
  - **Unit:** log level is configurable; default is INFO.
  - **Integration:** a request that takes 200 ms produces a log line
    with `duration_ms = 200 ± 20`. (Allowance for jitter.)
  - **Performance:** middleware overhead < 1 ms p95 (benchmarked).
  - **Regression:** no `print()` calls in `search/` or `indexer/`.
    Existing ruff config already flags `T201`; the test verifies.

**C7. Benchmarks wired into CI** (§4.9). A regression on any budget
item fails the build.

- *Tests:*
  - **Benchmark:** each row of the §4.9 table has a benchmark
    (already implemented in C1–C5) wired into the CI pipeline.
  - **Regression gate:** CI fails on any budget breach by more than
    a configurable margin (default 10%) to absorb noise.
  - **Coverage:** the benchmark suite itself is deterministic
    (mock encoder, in-memory Qdrant). Real-model benchmarks are
    gated behind an env var so CI without a GPU doesn't time out.

### Phase D — Polish

Items that depend on Phase C's measurements being real.

**D1. HNSW parameter sweep.** Tune `m`, `ef_construct`, `ef` against
the actual workload once the benchmark harness is in place.

- *Tests:*
  - **Benchmark:** sweep produces recall-vs-throughput numbers at
    three operating points. The chosen config is checked in.
  - **Regression:** the chosen config is the one C1's benchmark
    enforces going forward.

**D2. Payload size cap on `qdrant.search`.** Trim unneeded fields.

- *Tests:*
  - **Contract:** every search response fits within the documented
    size budget. A test asserts the wire-size of a typical response
    is below the cap.
  - **Benchmark:** query-latency budget still holds.

**D3. HTTP/2 server push / keep-alive tuning.** If the deployment
proxy supports it.

- *Tests:*
  - **Integration:** the proxy is configured to allow HTTP/2; the
    FastAPI app's HTTP/2 capability is asserted.
  - **Benchmark:** the C6 middleware shows the expected reduction in
    time-to-first-byte.

**D4. Precompression of static assets.** If not already handled by
the proxy.

- *Tests:*
  - **Contract:** `GET /_app/*` with `Accept-Encoding: br` returns
    the precompressed `.br` file with `Content-Encoding: br`.
  - **Integration:** the build step emits `.br` and `.gz` next to
    the originals; the test asserts their presence.

### Sequencing dependencies

- A → everything. Phase A items block B and C.
- B1 → desktop app. The desktop app cannot start until B1 lands.
- B4 → C1, C2, C3. Cache correctness must hold before perf work that
  touches it.
- C1 → C7. The benchmark harness must exist before regression CI.
- D items depend on C7's measurements being representative.
- Every phase item has explicit Tests bullets above. A PR that lands
  a phase item without its tests fails code review.

---

## 6. Self-consistency check

This plan is internally consistent if the following hold. Each item is
stated as a property a reviewer can verify, not a self-graded claim.

1. **Coverage (per-feature).** Every numbered subsection of §4 from
   4.1 through 4.11 is referenced by at least one phase item in §5.
2. **Coverage (cross-cutting).** §4.12 (test strategy), §4.13
   (engineering standards), and §4.14 (concurrency safety) apply to
   every phase item in §5 implicitly. They are not referenced by
   any single phase; they constrain all of them.
2. **Phasing.** Every phase item in §5 is referenced by at least one
   subsection of §4. No phase item exists without an upstream
   justification.
3. **Tests.** Every phase item in §5 has an explicit Tests sub-bullet
   block. A phase item with no tests is a phase item that doesn't
   merge.
4. **Dependencies.** Every dependency listed in §5's "Sequencing
   dependencies" subsection corresponds to a real ordering constraint
   in the phase descriptions.
5. **Dependency direction.** The dependency rules in §3 are testable:
   a test fails if the shared kernel imports anything from `search/`
   or `indexer/`, fails if `indexer/` imports from `search/`, fails
   if `search/` imports from `indexer/` outside the shared kernel's
   payload contract.
6. **Desktop readiness.** A reviewer can verify, by reading the
   shared-kernel and pipeline sections only, that a TypeScript +
   Electron consumer can be written that imports from these two and
   nothing else.
7. **Performance budgets.** Every row of the §4.9 table has a
   benchmark referenced by at least one phase item in §C.
8. **Backwards compatibility.** Every phase item that touches the
   public surface (API, CLI, on-disk format) lists the contract test
   that enforces its compat claim.
9. **Engineering standards.** Every discipline added in §4.13
   (mypy, lint, logging, errors, deps, ADRs, deprecations, public
   API) is enforced by a CI gate or fails code review.

---

## 7. Explicit deferrals

The following decisions are deferred by intent, not by oversight.
Each is named with the reason it isn't decided here, so a future
reader doesn't have to ask whether it was forgotten.

- ~~Specific smaller-SigLIP-2 model for the desktop product.~~ **Decided:**
  `ViT-L-16-SigLIP2-256` with the PyTorch + `open_clip` runtime
  (matches the web backend's stack). The registry entry shape (§4.3)
  is fixed; this specific entry lands when the desktop product's first
  PR opens.
- **Shared vs. separate Qdrant instance for the two products.** The
  schema-versioning work (§4.2) makes either deployment shape
  possible. The decision is operational and belongs with deployment
  topology, which is out of scope (§2).
- **Indexer as long-running service vs. CLI.** The pipeline-as-library
  shape (§4.4 / B1) supports both invocation patterns. The
  operational call is downstream of the refactor.
- **JSON cache as a fallback behind the new SQLite cache.** B4 lands
  SQLite; whether the JSON path stays as a degraded mode (for hosts
  without SQLite, which is none of the supported targets) is an
  operational call. Default in this plan: removed entirely.
- **Specific HNSW parameters** (`m`, `ef_construct`, `ef`). Tuned
  empirically in D1 against the actual workload. Picking now would
  be guessing.

---

## 8. Done =

The plan is complete when **every** item below is checkable as yes.
Each item has a single owner verification path so "done" isn't a
vibe.

1. **All phase items A1–D4 land and stay green in CI.**
   *Verify:* `git log --grep="^[A-D][1-7]"` shows a merged PR per
   item; `pytest` exits 0 on `main`; `ruff check` exits 0;
   `mypy` exits 0 on the kernel + registry + payload modules.
2. **Benchmark suite runs in CI on every PR.** Every row of §4.9
   has a corresponding benchmark. A regression beyond the configured
   noise margin fails the build.
   *Verify:* CI workflow file references the benchmark entrypoint;
   one test failure per budget row.
3. **The desktop product's first commit imports only the shared
   kernel and the indexer pipeline.** No `from search import …`.
   *Verify:* the desktop repo's first-commit `grep -r "from search"`
   returns empty.
4. **Adding a new API resource is a one-module change.** A reviewer
   can add a `routers/foo.py` and wire it into `app.py` without
   editing any other router module.
   *Verify:* a fixture PR adding a stub router touches exactly two
   files.
5. **Adding a model is a one-entry change.** No grep-and-replace.
   *Verify:* a fixture PR adding a registry entry leaves every
   other file unchanged. A grep test confirms no
   `VECTOR_DIM` / `_EMBED_DIM` / `1536` / `384` (resolution) lives
   outside the registry.
6. **Schema version migration runs without a wipe.** A v0 collection
   migrates to a v1 collection via the helper; both old and new
   paths are verified by the integration test in §A2.
   *Verify:* the migration test in `tests/test_migration.py` exits 0
   on a fixture v0 collection.
7. **Existing production deployment behaves correctly after every
   phase ships.** No URL or response field changes.
   *Verify:* `tests/test_openapi_stability.py` passes against the
   checked-in spec on every PR.
8. **All shared-kernel, registry, and payload modules pass
   `mypy --strict`.** The CI gate enforces this.
   *Verify:* the CI step `mypy --strict image_search_kernel` exits 0.
9. **Every phase item's Tests bullets have a corresponding test file
   or test class.** No untested change merges.
   *Verify:* a script that diffs phase-items against test names
   exits 0.
10. **ADRs for A1, A3, A2, B1, B5, C1 exist in `docs/adr/`.**
    *Verify:* `ls docs/adr/` shows six files.

---

## 9. Related documents

- `SCHEMA.md` (repo root) — point payload field reference. Stays in
  sync with the shared kernel's payload constants.
