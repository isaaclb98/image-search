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
from typing import Iterable

logger = logging.getLogger(__name__)

# Open_clip arch tag for the SigLIP2 ViT-gopt-16 model. Pairs with
# pretrained="webli". See https://huggingface.co/timm/ViT-gopt-16-SigLIP2-384.
DEFAULT_ARCH: str = "ViT-gopt-16-SigLIP2-384"
DEFAULT_PRETRAINED: str = "webli"


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
    ) -> None:
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
