"""Tests for the payload fingerprints used by search Diversity."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from indexer.fingerprints import (
    compute_fingerprints,
    content_sha256,
    dhash,
    hamming_distance,
)
from indexer.upsert import build_payload


def _image(path: Path, offset: int = 0) -> Path:
    image = Image.new("RGB", (48, 48), (20, 40, 60))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8 + offset, 8, 32 + offset, 32), fill=(220, 120, 40))
    image.save(path)
    return path


def test_content_sha256_is_deterministic_and_distinguishes_bytes(tmp_path):
    first = tmp_path / "one.bin"
    second = tmp_path / "two.bin"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    assert content_sha256(first) == content_sha256(second)
    second.write_bytes(b"different")
    assert content_sha256(first) != content_sha256(second)


def test_dhash_is_stable_and_nearby_images_have_small_distance(tmp_path):
    first = dhash(_image(tmp_path / "one.png"))
    second = dhash(_image(tmp_path / "two.png", offset=1))
    assert first and second
    distance = hamming_distance(first, second)
    assert distance is not None
    assert distance < 40


def test_compute_fingerprints_handles_invalid_image_bytes(tmp_path):
    path = tmp_path / "not-an-image.jpg"
    path.write_bytes(b"payload")
    fingerprints = compute_fingerprints(path)
    assert fingerprints["content_sha256"]
    assert fingerprints["dhash"] is None


def test_compute_fingerprints_has_both_values_for_image(tmp_path):
    fingerprints = compute_fingerprints(_image(tmp_path / "photo.png"))
    assert fingerprints["content_sha256"]
    assert fingerprints["dhash"]


def test_build_payload_wires_diversity_fingerprints(tmp_path):
    payload = build_payload(
        _image(tmp_path / "photo.png"),
        shard="",
        model_name="test",
        model_revision="",
        collection="personal",
    )
    assert payload["content_sha256"]
    assert payload["dhash"]
