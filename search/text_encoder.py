"""
search/text_encoder.py

Thin wrapper around the model registry's text embedder (§A3).

The actual SigLIP2 loading lives in
`image_search_kernel._real_models.OpenClipEmbedder`. This module
exists to preserve the historical `TextEncoder(...)` API and to
implement prompt-composition (positive / negative prompt mean +
difference) on top of the registered text embedder.

Mock mode is dispatched via the `mock-1536` registry entry. The
local `_mock_embed` function is gone — `MockEmbedder` in the registry
produces deterministic unit-norm vectors from any text input.

The `_normalize_query_for_siglip2` and prompt-composition helpers
are pure-Python and remain here; they're model-agnostic.

This module imports nothing from torch / open_clip / transformers.
The kernel owns those imports.
"""

from __future__ import annotations

import functools
import logging

from image_search_kernel.vectors import l2_normalize, mean_vector

logger = logging.getLogger(__name__)

# Default model name registered with the kernel.
DEFAULT_MODEL_NAME: str = "ViT-gopt-16-SigLIP2-384"
# Mock entry registered by `image_search_kernel.registry.get_default_registry`.
MOCK_MODEL_NAME: str = "mock-1536"


def _normalize_query_for_siglip2(text: str) -> str:
    """Lowercase the query.

    SigLIP2's text tower was trained on lowercased text. Without
    this step a query like "Cat" produces a cosine similarity of
    ~0.1-0.15 to image vectors, vs. the scorer's expected ~0.4-0.6.
    """
    return text.lower()


class TextEncoder:
    """
    Adapter: routes `embed` / `embed_multi` to the text embedder
    registered for `model_name` in the model registry.

    The `arch` parameter is interpreted as the model name in the
    registry. The `pretrained` argument is accepted for backward
    compatibility and ignored. The `test_mode` flag selects the
    `mock-1536` registry entry instead.
    """

    def __init__(
        self,
        arch: str = DEFAULT_MODEL_NAME,
        pretrained: str = "webli",
        device: str = "cpu",
        test_mode: bool = False,
    ) -> None:
        self._requested_arch = arch
        self._pretrained = pretrained
        self.device = device
        self.test_mode = test_mode

        from image_search_kernel.registry import get as _registry_get

        if test_mode:
            try:
                self._embedder = _registry_get(MOCK_MODEL_NAME).text
            except Exception:  # noqa: BLE001
                self._embedder = _registry_get(arch).text
        else:
            self._embedder = _registry_get(arch).text

    @property
    def dim(self) -> int:
        return self._embedder.dim

    @property
    def resolution(self) -> int:
        # Text encoder has no image resolution; return the model's
        # paired vision resolution for symmetry with vision_encoder.
        from image_search_kernel.registry import get as _registry_get

        try:
            return _registry_get(self._requested_arch).resolution
        except Exception:  # noqa: BLE001
            return 384

    def embed(self, text: str) -> list[float]:
        return self._embedder.embed_text(_normalize_query_for_siglip2(text))

    def embed_multi(self, positives: list[str], negatives: list[str]) -> list[float]:
        """Compose positive and negative prompts into one unit-norm vector.

        Mirrors isaac-image-scoring's prompt semantics: mean each
        side's normalized text embeddings, subtract negatives from
        positives, L2-normalize the final vector for Qdrant cosine
        search.
        """
        pos_embeds = [
            self._embedder.embed_text(_normalize_query_for_siglip2(t))
            for t in positives
        ]
        neg_embeds = [
            self._embedder.embed_text(_normalize_query_for_siglip2(t))
            for t in negatives
        ]
        if pos_embeds and neg_embeds:
            pos_mean = mean_vector(pos_embeds)
            neg_mean = mean_vector(neg_embeds)
            return l2_normalize([p - n for p, n in zip(pos_mean, neg_mean, strict=False)])
        if pos_embeds:
            return l2_normalize(mean_vector(pos_embeds))
        if neg_embeds:
            return l2_normalize([-v for v in mean_vector(neg_embeds)])
        raise ValueError("at least one prompt is required")


# --- Singleton management (process-global) -----------------------------

_encoder_singleton: TextEncoder | None = None


def get_encoder(test_mode: bool | None = None) -> TextEncoder:
    """Return the process-global TextEncoder, initializing on first call.

    `test_mode` is consumed only on the first call; subsequent calls
    return the cached instance. Pass `None` to use the environment
    default (`SEARCH_TEST_MODE=1` enables mock mode).
    """
    global _encoder_singleton
    if _encoder_singleton is None:
        import os

        if test_mode is None:
            test_mode = os.environ.get("SEARCH_TEST_MODE") == "1"
        arch = os.environ.get("MODEL_NAME", DEFAULT_MODEL_NAME)
        device = os.environ.get("TEXT_ENCODER_DEVICE", "cpu")
        _encoder_singleton = TextEncoder(
            arch=arch, pretrained="webli", device=device, test_mode=test_mode,
        )
    return _encoder_singleton


def reset_encoder_for_tests() -> None:
    """Drop the singleton so the next call reinitializes (used in test fixtures)."""
    global _encoder_singleton
    _encoder_singleton = None


@functools.lru_cache(maxsize=512)
def _embed_query_cached(text: str) -> tuple[float, ...]:
    vec = get_encoder().embed(text)
    return tuple(vec)


def clear_cache() -> None:
    """Drop the embed-query LRU cache. Useful in tests when the encoder singleton is reset."""
    _embed_query_cached.cache_clear()


def embed_query(text: str) -> list[float]:
    """Module-level entry point: encode a single text query."""
    return list(_embed_query_cached(text))


def _canonical_prompt_tuple(prompts: tuple[str, ...]) -> tuple[str, ...]:
    """Strip, lowercase, and case-insensitively dedupe prompts.

    The cache is keyed on the SigLIP2-normalized prompt text so
    "Cat" and "cat" share one model invocation.
    """
    seen: set[str] = set()
    out: list[str] = []
    for prompt in prompts:
        text = _normalize_query_for_siglip2(prompt.strip())
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def embed_query_multi(positives: tuple[str, ...], negatives: tuple[str, ...]) -> list[float]:
    """Embed composed positive/negative prompts via the singleton encoder.

    Cache keys are canonicalized before the LRU boundary so casing and
    accidental whitespace do not fragment the cache.
    """
    canonical_positives = _canonical_prompt_tuple(positives)
    canonical_negatives = _canonical_prompt_tuple(negatives)
    return list(_embed_query_multi_cached(canonical_positives, canonical_negatives))


@functools.lru_cache(maxsize=512)
def _embed_query_multi_cached(
    positives: tuple[str, ...], negatives: tuple[str, ...],
) -> tuple[float, ...]:
    vec = get_encoder().embed_multi(list(positives), list(negatives))
    return tuple(vec)


def clear_cache_multi() -> None:
    """Drop the multi-prompt LRU cache. Useful in tests."""
    _embed_query_multi_cached.cache_clear()
