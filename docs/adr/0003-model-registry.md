# ADR-0003: Model registry + `Embedder` Protocol

**Status:** Accepted.
**Date:** 2026-08.
**Related:** `docs/backend-refactor-plan.md` §4.3, §4.3.1.

## Context

Before this refactor, model knowledge was scattered:

- `VECTOR_DIM = 1536` in `indexer/upsert.py`.
- `_EMBED_DIM = 1536` in `indexer/vision_encoder.py` and `search/text_encoder.py`.
- `SIGLIP_RESOLUTION = 384` in `indexer/image_loader.py`.
- `SIGLIP2-MEAN/STD` constants in `indexer/image_loader.py`.
- `_CENTROID_MODEL_COMPAT` mapping in `search/config.py` duplicating dim knowledge.

A second, smaller SigLIP-2 variant was planned for the desktop product. A second variant means two models with different dims and resolutions. The hardcoded approach doesn't scale: each new model is a multi-file grep with no compile-time check.

## Decision

Introduce `image_search_kernel.registry` with:

- **`Embedder` Protocol** — the only model-specific interface in the codebase. Properties: `dim`, `resolution`. Methods: `embed_text`, `embed_texts`, `embed_image`, `embed_images`. Concrete implementations satisfy it via duck-typing (Protocol is `@runtime_checkable`).
- **`ModelSpec` dataclass** — pins a model name to `dim`, `resolution`, `revision`, plus paired `text` and `vision` `Embedder` instances.
- **`Registry`** — `register(ModelSpec)`, `get(name) -> ModelSpec`, `has(name)`, `names()`. Raises `ModelNotFoundError` (KeyError subclass) on miss.
- **`MockEmbedder`** — deterministic mock that produces a unit-norm vector of `dim` floats from a seed derived from the input. Always available; safe to import on any host.
- **`OpenClipEmbedder`** — adapter that wraps `open_clip.create_model_from_pretrained()` behind the `Embedder` Protocol. Lazy-loads weights on first `embed_*` call (cheap registry init, model-load cost paid on first use).
- **Conditional registration** — `_real_models.register_into(registry)` is called once on first `get_default_registry()`. Wrapped in `try/except ImportError`. The kernel package itself imports cleanly on a host with no torch/open_clip/transformers; only the default-registry first-touch fails-soft into the mock-only mode.

`indexer/vision_encoder.py` and `search/text_encoder.py` are thin wrappers over the registry:

- `VisionEncoder(arch, pretrained, device, *, test_mode=False)` looks up the registry entry by name. `test_mode=True` selects `mock-1536` instead.
- `TextEncoder(...)` does the same on the text side.
- Both delegate all real inference to the registered embedder; they exist for backward compatibility with existing call sites.

`build_payload` in `indexer/upsert.py` looks up `model_dim` from the registry by `model_name` if not passed explicitly. Tests register a `test` model entry in `conftest.py` so existing tests that pass `model_name="test"` continue to work.

Real-model registrations shipped today: `ViT-gopt-16-SigLIP2-384` (web backend, conditional) and `ViT-L-16-SigLIP2-256` (desktop product, conditional). `mock-1536` is always registered.

## Consequences

- **Positive:** Adding a model is a registry entry plus a config flag. No grep-and-replace.
- **Positive:** The desktop product's smaller model is a different registry entry; the indexer pipeline (§B1) consumes it through the same `Embedder` Protocol. No code changes to `indexer/`, `search/`, or the kernel.
- **Positive:** Tests run on CPU-only hosts without GPU or model weights — `mock-1536` is always available.
- **Negative:** The kernel package has an import dependency on `Pillow` (for the `Embedder.embed_image` type hint). PIL is already a project dep, so no new dep — but the kernel is no longer stdlib-only.
- **Negative:** Real-model code (`open_clip`, `torch`) lives in `image_search_kernel/_real_models.py` and is conditionally imported. A test that constructs the registry without the runtime available will see only `mock-1536`. This is documented in `_real_models.py` and verified by `test_kernel_is_importable_on_cpu_only_host`.

## Architectural invariants enforced by tests

- **No file outside `image_search_kernel/registry.py`, `_real_models.py`, `__init__.py`, or `indexer/upsert.py` (single deprecated `VECTOR_DIM`) references model dim/resolution literals or constants.** Enforced by `tests/test_architecture.py:test_no_hardcoded_model_dim_outside_registry` (AST-based; ignores docstrings and arithmetic).
- **No file outside `image_search_kernel/_real_models.py`, `search/centroids.py`, or the centroids test fixtures imports `torch` / `open_clip` / `transformers` / `timm`.** Enforced by `tests/test_architecture.py:test_no_ml_runtime_imports_outside_kernel_real_models`.
- **The kernel package imports cleanly with no torch/open_clip.** Enforced by `test_kernel_is_importable_on_cpu_only_host`.

A future PR that hardcodes a dim elsewhere, imports torch anywhere except the registry, or breaks the CPU-only import path fails CI, not code review.

## Alternatives considered

- **Per-model classes (`GoptEmbedder`, `BaseEmbedder`, ...).** Rejected: each new model adds a class. The Protocol is what the registry abstracts over.
- **Config-file-driven registry (`models.yaml`).** Rejected: configuration as code is the right call for a small, well-known set of models. Config files introduce parsing, validation, and staleness concerns.
- **Embedder as a function, not a class.** Rejected: a class carries `dim` and `resolution` without consumers passing them separately, which prevents accidental dim mismatches at call time.

## Verification

- `tests/test_unit_critical_modules.py::TestVisionEncoderMockPath`: 3/3 passing (mock round-trip, dim correctness, determinism).
- `tests/test_architecture.py`: AST-based regression tests passing.
- `tests/test_run_pipeline.py::test_set_active_model_persists_across_calls`: end-to-end with the registry's `mock-1536`.
