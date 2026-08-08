"""
search/text_encoder.py

SigLIP2 text tower wrapper for the search app.

Uses `open_clip` to load the model — same loader as `isaac-image-scoring`,
so the text-embedding space matches what the indexer produced with
`indexer.vision_encoder`. A query like "woman in white dress" produces
a vector directly comparable to the indexed image vectors.

In test mode (SEARCH_TEST_MODE=1), the constructor short-circuits
to a deterministic mock so tests don't need GPU/HF downloads.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import math
import os
from typing import Any

logger = logging.getLogger(__name__)

# Open_clip arch tag for the SigLIP2 ViT-gopt-16 model. Pairs with
# pretrained="webli". See https://huggingface.co/timm/ViT-gopt-16-SigLIP2-384.
DEFAULT_ARCH: str = "ViT-gopt-16-SigLIP2-384"
DEFAULT_PRETRAINED: str = "webli"
_EMBED_DIM: int = 1536


def _mock_embed(text: str) -> list[float]:
    """
    Deterministic mock embedding for tests.

    hashlib.sha512(text) -> 64 bytes -> repeat to 1536 dims -> L2-normalize.
    Same recipe as `isaac-image-scoring`'s test mock for parity.
    """
    seed = hashlib.sha512(text.encode("utf-8")).digest()
    raw = (seed * ((_EMBED_DIM * 4 // len(seed)) + 1))[: _EMBED_DIM * 4]
    ints = [int.from_bytes(raw[i : i + 4], "big") for i in range(0, len(raw), 4)]
    # Map to [-1, 1]
    vals = [(v / 0xFFFFFFFF) * 2.0 - 1.0 for v in ints]
    # L2 normalize
    norm = sum(v * v for v in vals) ** 0.5 or 1.0
    return [v / norm for v in vals]


def _normalize_query_for_siglip2(text: str) -> str:
    """
    SigLIP2's text encoder was trained on lowercased text. The
    open_clip tokenizer does NOT lowercase automatically — feed
    it mixed-case text and the embedding degrades. isaac-image-
    scoring does the same lowercase at `aesthetic_scorer.py:230`
    (its docstring is explicit: "padding='max_length', max_length=64,
    explicit .lower() ... Case-sensitive text severely degrades
    embeddings").

    Concretely, without this step a query like "Cat" produces a
    cosine similarity of ~0.1-0.15 to image vectors, vs. the
    scorer's expected ~0.4-0.6 (which lowercases). The scores
    were the only symptom — the search was returning relevant
    results, just with very low confidence values.
    """
    return text.lower()


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    """Return the elementwise mean for a non-empty list of equal-length vectors."""
    count = len(vectors)
    return [sum(values) / count for values in zip(*vectors, strict=False)]


def _l2_normalize(values: list[float]) -> list[float]:
    """Normalize a vector to unit length, matching torch.nn.functional.normalize."""
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def _canonical_prompt_tuple(prompts: tuple[str, ...]) -> tuple[str, ...]:
    """
    Strip, lowercase, and case-insensitively dedupe prompts before cache lookup.

    The API layer preserves display casing for its response echo. The encoder
    cache is intentionally keyed on the SigLIP2-normalized prompt text so
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


class TextEncoder:
    """
    Loads SigLIP2 via open_clip and embeds text queries to unit-norm
    1536-dim vectors compatible with the indexer's image vectors.
    """

    def __init__(
        self,
        arch: str = DEFAULT_ARCH,
        pretrained: str = DEFAULT_PRETRAINED,
        device: str = "cpu",
        test_mode: bool = False,
    ) -> None:
        self.test_mode = test_mode
        if test_mode:
            logger.info("TextEncoder: test mode, using mock embedder")
            return

        import open_clip

        self.arch = arch
        self.pretrained = pretrained
        self.model, _, _ = open_clip.create_model_and_transforms(
            arch, pretrained=pretrained, device=device
        )
        self.tokenizer = open_clip.get_tokenizer(arch)
        self.model.eval()
        self.device = device
        logger.info("loaded SigLIP2 text tower %s/%s on %s", arch, pretrained, device)

    def embed(self, text: str) -> list[float]:
        if self.test_mode:
            return _mock_embed(text)

        import torch
        import torch.nn.functional as F

        with torch.no_grad():
            tokens = self.tokenizer([_normalize_query_for_siglip2(text)]).to(self.device)
            feats = self.model.encode_text(tokens)
            feats = F.normalize(feats, p=2, dim=-1)
        return feats[0].cpu().tolist()

    def embed_multi(self, positives: list[str], negatives: list[str]) -> list[float]:
        """
        Compose positive and negative prompts into one unit-norm query vector.

        Mirrors isaac-image-scoring's prompt semantics: average each side's
        normalized text embeddings, subtract negatives from positives, then
        L2-normalize the final vector for Qdrant cosine search.
        """
        if self.test_mode:
            pos_embeds = [_mock_embed(text) for text in positives]
            neg_embeds = [_mock_embed(text) for text in negatives]
            if pos_embeds and neg_embeds:
                pos_mean = _mean_vector(pos_embeds)
                neg_mean = _mean_vector(neg_embeds)
                return _l2_normalize([p - n for p, n in zip(pos_mean, neg_mean, strict=False)])
            if pos_embeds:
                return _l2_normalize(_mean_vector(pos_embeds))
            if neg_embeds:
                neg_mean = _mean_vector(neg_embeds)
                return _l2_normalize([-v for v in neg_mean])
            raise ValueError("at least one prompt is required")

        import torch
        import torch.nn.functional as F

        def encode_texts(texts: list[str]) -> Any:
            if not texts:
                return None
            toks = self.tokenizer([t.lower() for t in texts]).to(self.device)
            feats = self.model.encode_text(toks)
            return F.normalize(feats, p=2, dim=-1)

        with torch.no_grad():
            pos_embeds = encode_texts(positives)
            neg_embeds = encode_texts(negatives)
            if pos_embeds is not None and neg_embeds is not None:
                combined = pos_embeds.mean(dim=0) - neg_embeds.mean(dim=0)
            elif pos_embeds is not None:
                combined = pos_embeds.mean(dim=0)
            elif neg_embeds is not None:
                combined = -neg_embeds.mean(dim=0)
            else:
                raise ValueError("at least one prompt is required")
            combined = F.normalize(combined, p=2, dim=0)
        return combined.cpu().tolist()


# ── Module-level singleton (lazy) ──────────────────────────────────────────

_encoder_singleton: TextEncoder | None = None


def get_encoder(test_mode: bool | None = None) -> TextEncoder:
    """
    Module-level singleton accessor. Lazy-loads on first call.
    """
    global _encoder_singleton
    if _encoder_singleton is None:
        if test_mode is None:
            test_mode = bool(os.environ.get("SEARCH_TEST_MODE"))
        arch = os.environ.get("MODEL_NAME", DEFAULT_ARCH)
        # Optional second knob: MODEL_PRETRAINED. Defaults to "webli".
        pretrained = os.environ.get("MODEL_PRETRAINED", DEFAULT_PRETRAINED)
        device = os.environ.get("DEVICE", "cpu")
        _encoder_singleton = TextEncoder(
            arch=arch, pretrained=pretrained, device=device, test_mode=test_mode
        )
    return _encoder_singleton


def reset_encoder_for_tests() -> None:
    """Drop the singleton so the next call reinitializes (used in test fixtures)."""
    global _encoder_singleton
    _encoder_singleton = None
    clear_cache_multi()


def embed_query(text: str) -> list[float]:
    """
    Embed a single text query via the singleton encoder.

    Module-level convenience so callers (e.g. search.app) don't have to
    reach into the singleton themselves.

    Wrapped in an LRU cache so repeated queries (e.g. a user re-typing
    the same search) don't re-run the model. Cache size is 256 — covers
    a session's worth of typical queries without growing unbounded.
    """
    return list(_embed_query_cached(text))


@functools.lru_cache(maxsize=256)
def _embed_query_cached(text: str) -> tuple[float, ...]:
    return tuple(get_encoder().embed(text))


def clear_cache() -> None:
    """Clear the embed_query LRU cache (called between tests)."""
    _embed_query_cached.cache_clear()


def embed_query_multi(positives: tuple[str, ...], negatives: tuple[str, ...]) -> list[float]:
    """
    Embed composed positive/negative prompts via the singleton encoder.

    Cache keys are canonicalized before the LRU boundary so casing and
    accidental whitespace do not fragment the cache.
    """
    canonical_positives = _canonical_prompt_tuple(positives)
    canonical_negatives = _canonical_prompt_tuple(negatives)
    return list(_embed_query_multi_cached(canonical_positives, canonical_negatives))


@functools.lru_cache(maxsize=256)
def _embed_query_multi_cached(
    positives: tuple[str, ...],
    negatives: tuple[str, ...],
) -> tuple[float, ...]:
    return tuple(get_encoder().embed_multi(list(positives), list(negatives)))


def clear_cache_multi() -> None:
    """Clear the multi-prompt LRU cache (called between tests)."""
    _embed_query_multi_cached.cache_clear()
