"""
image_search_kernel._real_models

Real-model registry entries. Imported lazily by `image_search_kernel.registry`
so that the kernel package itself is importable on hosts without
`torch` / `open_clip` / `transformers` installed.

If any required runtime is missing, importing this module raises
`ImportError`. The caller (`_try_register_real_models`) catches it
and proceeds with the mock-only registry.
"""

from __future__ import annotations

import open_clip
import torch
import transformers  # noqa: F401

from image_search_kernel.registry import ModelSpec, Registry

__all__ = ["register_into"]


class OpenClipEmbedder:
    """Adapter: wraps an open_clip model + preprocess into the Embedder Protocol.

    Lazy-loads the actual weights on first `embed_*` call (not on
    `__init__`), so the registry init stays cheap. The first text or
    image call pays the model-load cost; subsequent calls hit the
    cached model.

    Not thread-safe by default; the model-load guard uses a
    `threading.Lock`. Concurrent inference requests are serialized
    through the model forward pass.
    """

    def __init__(
        self,
        *,
        arch_tag: str,
        pretrained: str,
        dim: int,
        resolution: int,
    ) -> None:
        self._arch_tag = arch_tag
        self._pretrained = pretrained
        self._dim = dim
        self._resolution = resolution
        self._model = None
        self._tokenizer = None
        self._preprocess = None
        # Round‑15: honour `DEVICE` env var (defaults to cpu). When set
        # to `cuda` and a GPU is available, the loaded model is moved
        # to that device so image embedding can actually run on the
        # RTX 3080 instead of the CPU.
        import os as _os
        _dev = _os.environ.get("DEVICE", "cpu").strip().lower()
        if _dev == "cuda" and __import__("torch").cuda.is_available():
            self._device = "cuda"
        else:
            self._device = "cpu"
        self._lock = __import__("threading").Lock()

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def resolution(self) -> int:
        return self._resolution

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            model, preprocess = open_clip.create_model_from_pretrained(
                f"hf-hub:{self._arch_tag}",
            )
            tokenizer = open_clip.get_tokenizer(f"hf-hub:{self._arch_tag}")
            self._model = model.eval().to(self._device)
            self._preprocess = preprocess
            self._tokenizer = tokenizer

    def embed_text(self, text: str) -> list[float]:
        self._ensure_loaded()
        assert self._model is not None and self._tokenizer is not None  # noqa: S101 — type-narrowing after _ensure_loaded
        tokens = self._tokenizer([text])
        tokens = tokens.to(self._device)
        with torch.no_grad():
            features = self._model.encode_text(tokens)
            features = features / features.norm(dim=-1, keepdim=True)
        return features[0].tolist()

    def embed_texts(self, texts) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]

    def embed_image(self, image) -> list[float]:
        self._ensure_loaded()
        assert self._model is not None and self._preprocess is not None  # noqa: S101 — type-narrowing after _ensure_loaded
        from PIL import Image as _PILImage

        if not isinstance(image, _PILImage.Image):
            raise TypeError(f"expected PIL.Image, got {type(image).__name__}")
        tensor = self._preprocess(image).unsqueeze(0).to(self._device)
        with torch.no_grad():
            features = self._model.encode_image(tensor)
            features = features / features.norm(dim=-1, keepdim=True)
        return features[0].tolist()

    def embed_images(self, images) -> list[list[float]]:
        return [self.embed_image(img) for img in images]


def register_into(registry: Registry) -> None:
    """Register the real-model entries shipped with the kernel.

    Today: ViT-gopt-16-SigLIP2-384 (web backend) plus
    ViT-L-16-SigLIP2-256 (desktop). Each uses the same adapter; both
    register the same `text`/`vision` instances because the model is
    shared between text and image in the SigLIP-2 family.
    """

    gopt_embedder = OpenClipEmbedder(
        arch_tag="timm/ViT-gopt-16-SigLIP2-384",
        pretrained="webli",
        dim=1536,
        resolution=384,
    )
    registry.register(ModelSpec(
        name="ViT-gopt-16-SigLIP2-384",
        dim=1536,
        resolution=384,
        revision="webli",
        text=gopt_embedder,
        vision=gopt_embedder,
    ))

    l_embedder = OpenClipEmbedder(
        arch_tag="timm/ViT-L-16-SigLIP2-256",
        pretrained="webli",
        dim=1024,
        resolution=256,
    )
    registry.register(ModelSpec(
        name="ViT-L-16-SigLIP2-256",
        dim=1024,
        resolution=256,
        revision="webli",
        text=l_embedder,
        vision=l_embedder,
    ))
