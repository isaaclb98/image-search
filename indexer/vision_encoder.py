"""
indexer/vision_encoder.py

SigLIP2 vision tower wrapper for the indexer.

Uses `open_clip` to load the model — same loader as `isaac-image-scoring`,
so the embedding space is identical between the two systems. An image
indexed here can be scored there without re-embedding.

`open_clip.create_model_and_transforms(arch, pretrained)` downloads the
weights from the timm HuggingFace Hub repo (e.g.
`timm/ViT-gopt-16-SigLIP2-384`), but it reads the open_clip-format files
(`open_clip_config.json`, `open_clip_model.safetensors`) — NOT the
transformers `config.json` / `model.safetensors`. So `transformers.AutoModel
.from_pretrained` cannot load this model.

Torch is imported lazily so the file is importable on machines without GPU.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

logger = logging.getLogger(__name__)

# Open_clip arch tag for the SigLIP2 ViT-gopt-16 model. Pairs with
# pretrained="webli". See https://huggingface.co/timm/ViT-gopt-16-SigLIP2-384.
DEFAULT_ARCH: str = "ViT-gopt-16-SigLIP2-384"
DEFAULT_PRETRAINED: str = "webli"

_EMBED_DIM: int = 1536


def _mock_image_embed(seed: int) -> list[float]:
    """Deterministic unit-norm mock embedding for tests.

    Same shape (1536-dim, L2-normalized) as real SigLIP2 output so
    Qdrant cosine math behaves; values depend on `seed` so different
    images embed differently. Avoids loading the multi-GB ViT in tests.
    """
    import hashlib
    import math

    digest = hashlib.sha512(str(seed).encode()).digest()  # 64 bytes
    raw = [digest[i % len(digest)] / 255.0 - 0.5 for i in range(_EMBED_DIM)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


class VisionEncoder:
    """
    Loads SigLIP2 via open_clip and produces unit-norm 1536-dim vectors
    for batches of PIL images.

    Callers should letterbox images to 384x384 before passing them in
    (see `indexer.image_loader.letterbox_resize`). The open_clip
    `preprocess` will then be a no-op for size and just normalize.
    """

    def __init__(
        self,
        arch: str = DEFAULT_ARCH,
        pretrained: str = DEFAULT_PRETRAINED,
        device: str = "cpu",
        test_mode: bool | None = None,
    ) -> None:
        import os
        if test_mode is None:
            test_mode = bool(os.environ.get("SEARCH_TEST_MODE"))
        self.test_mode = test_mode
        if test_mode:
            logger.info("VisionEncoder: test mode, using mock embedder")
            return

        import open_clip

        self.arch = arch
        self.pretrained = pretrained
        self.device = device
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            arch, pretrained=pretrained, device=device
        )
        self.model.eval()
        logger.info("loaded SigLIP2 %s/%s on %s", arch, pretrained, device)

    def embed_batch(self, images: Iterable) -> list[list[float]]:
        """
        Embed a batch of PIL images (already letterboxed to 384x384).
        Returns unit-norm 1536-dim vectors.
        """

        images = list(images)
        if getattr(self, "test_mode", False):
            # Seed the mock from the image CONTENT (bytes hash), not the
            # batch index, so different images embed differently and a
            # re-embedded (changed) file gets a new vector.
            out = []
            for img in images:
                try:
                    import hashlib
                    import io
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    seed = int.from_bytes(hashlib.sha256(buf.getvalue()).digest()[:8], "big")
                except Exception:
                    seed = id(img)
                out.append(_mock_image_embed(seed))
            return out
        if not images:
            return []
        import torch
        import torch.nn.functional as F

        images = list(images)
        if not images:
            return []

        with torch.no_grad():
            tensors = torch.stack(
                [self.preprocess(img) for img in images]
            ).to(self.device)
            feats = self.model.encode_image(tensors)
            feats = F.normalize(feats, p=2, dim=-1)
        return feats.cpu().tolist()

    def embed_one(self, image) -> list[float]:
        """Convenience: embed a single image."""
        return self.embed_batch([image])[0]
