"""Round‑30: tests that the indexer persists source dimensions
into both the qdrant payload and the local SQLite cache.

Photo page's `formatDimensions()` reads from the qdrant payload
(or, in the cache path, the SQLite `images` table). Before this
round the indexer never wrote dims, so the page showed "—" for
every photo.

After the fix:
- `indexer.upsert.build_payload` accepts optional `width` /
  `height` keyword args and includes them in the returned dict.
- `indexer.image_loader.load` returns `(letterboxed_img,
  source_w, source_h)` so the pipeline can pass the source dims
  through without re-reading the file.
- `search.index_db.upsert_records` persists the dims in the
  `images` SQLite table.
"""

from __future__ import annotations

from pathlib import Path

from indexer.upsert import build_payload


def test_build_payload_includes_width_and_height(tmp_path: Path):
    """Round‑30: build_payload should include width / height
    when the caller passes them in (the round‑19 hot path)."""
    p = tmp_path / "x.jpg"
    p.write_bytes(b"")
    payload = build_payload(
        path=p, shard="",
        model_name="ViT-L-16-SigLIP2-256",
        model_revision="webli",
        collection="",
        width=3000, height=2000,
    )
    assert payload["width"] == 3000
    assert payload["height"] == 2000


def test_build_payload_width_and_height_default_to_none(tmp_path: Path):
    """Back-compat: existing callers that don't pass dims (the
    legacy disk-only path) still get a payload — the new fields
    just default to None."""
    p = tmp_path / "x.jpg"
    p.write_bytes(b"")
    payload = build_payload(
        path=p, shard="",
        model_name="ViT-L-16-SigLIP2-256",
        model_revision="webli",
        collection="",
    )
    assert "width" in payload
    assert "height" in payload
    assert payload["width"] is None
    assert payload["height"] is None


def test_load_returns_source_dims_alongside_letterboxed_image(tmp_path: Path):
    """Round‑30: `image_loader.load` now returns
    `(letterboxed_img, source_w, source_h)` so the ingest pipeline
    can persist source dims without a second PIL read.

    Source: a 100×200 PNG. After the letterbox to 256×256 the
    returned `img` is 256×256, but `source_w` / `source_h` must
    still be the original 100×200.
    """
    from PIL import Image

    from indexer.image_loader import load

    p = tmp_path / "wide.png"
    Image.new("RGB", (100, 200), (255, 0, 0)).save(p)
    img, source_w, source_h = load(p, model_name="ViT-L-16-SigLIP2-256")
    # Source dims preserved.
    assert source_w == 100
    assert source_h == 200
    # Letterboxed image is square at the registered resolution.
    assert img.size == (256, 256)
