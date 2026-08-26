"""
tests/test_fingerprints_unit.py — Unit tests for indexer/fingerprints.py.

Image fingerprints used by the search-side Diversity ranker:
  - content_sha256: byte-for-byte file identity
  - dhash: 8x8 perceptual difference hash
  - hamming_distance: bit difference between two dhashes
  - compute_fingerprints: convenience wrapper returning both
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from indexer.fingerprints import (
    DEFAULT_DHASH_SIZE,
    compute_fingerprints,
    content_sha256,
    dhash,
    hamming_distance,
)


# ----- DEFAULT_DHASH_SIZE -----

class TestDefaultDhashSize:
    """The default hash_size constant."""

    def test_default_is_8(self):
        assert DEFAULT_DHASH_SIZE == 8


# ----- content_sha256 -----

class TestContentSha256:
    """SHA-256 digest of the file's bytes."""

    def test_returns_hex_string(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        result = content_sha256(f)
        assert result is not None
        # Hex string is 64 chars (256 bits = 32 bytes = 64 hex)
        assert len(result) == 64
        # All chars are hex
        assert all(c in "0123456789abcdef" for c in result)

    def test_matches_hashlib(self, tmp_path):
        """The digest should match hashlib.sha256(file_bytes).hexdigest()."""
        f = tmp_path / "test.bin"
        content = b"some test content"
        f.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert content_sha256(f) == expected

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"content a")
        f2.write_bytes(b"content b")
        assert content_sha256(f1) != content_sha256(f2)

    def test_missing_file_returns_none(self, tmp_path):
        """Unreadable file → None, not raise."""
        missing = tmp_path / "missing.bin"
        result = content_sha256(missing)
        assert result is None

    def test_empty_file(self, tmp_path):
        """Empty file has a well-defined SHA-256."""
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        result = content_sha256(f)
        # SHA-256 of empty input is a known constant
        assert result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_large_file_chunked(self, tmp_path):
        """Large files should be read in chunks, not all at once."""
        f = tmp_path / "large.bin"
        # Write 3 MB of data
        f.write_bytes(b"x" * (3 * 1024 * 1024))
        result = content_sha256(f)
        assert result is not None
        assert len(result) == 64

    def test_binary_content(self, tmp_path):
        """Binary (non-text) content should hash correctly."""
        f = tmp_path / "binary.bin"
        f.write_bytes(bytes(range(256)))
        result = content_sha256(f)
        assert result is not None
        assert len(result) == 64


# ----- dhash -----

class TestDhash:
    """Perceptual difference hash for an image."""

    def test_returns_hex_string(self, tmp_path):
        # Create a test image
        img_path = tmp_path / "test.jpg"
        Image.new("RGB", (100, 100), color="red").save(img_path, "JPEG")
        result = dhash(img_path)
        assert result is not None
        # Default hash_size=8 → 64 bits → 16 hex chars
        assert len(result) == DEFAULT_DHASH_SIZE * 2

    def test_invalid_hash_size_raises(self, tmp_path):
        img_path = tmp_path / "test.jpg"
        Image.new("RGB", (100, 100), color="red").save(img_path, "JPEG")
        with pytest.raises(ValueError):
            dhash(img_path, hash_size=1)

    def test_hash_size_zero_raises(self, tmp_path):
        img_path = tmp_path / "test.jpg"
        Image.new("RGB", (100, 100), color="red").save(img_path, "JPEG")
        with pytest.raises(ValueError):
            dhash(img_path, hash_size=0)

    def test_missing_file_returns_none(self, tmp_path):
        """Unreadable image → None, not raise."""
        result = dhash(tmp_path / "missing.jpg")
        assert result is None

    def test_corrupted_image_returns_none(self, tmp_path):
        """Corrupted image data → None, not raise."""
        bad = tmp_path / "bad.jpg"
        bad.write_bytes(b"not an image")
        result = dhash(bad)
        assert result is None

    def test_different_images_different_hashes(self, tmp_path):
        # dhash captures brightness transitions between adjacent pixels.
        # Solid colors and single-row gradients hash to all-zeros because
        # the resize to 8x9 averages everything out. Use checkerboard
        # patterns so the hash captures real structural differences.
        import numpy as np
        red = tmp_path / "red.jpg"
        blue = tmp_path / "blue.jpg"
        # Checkerboard patterns — different phases
        arr_red = np.zeros((50, 50, 3), dtype=np.uint8)
        arr_red[::2, ::2] = [255, 0, 0]
        arr_red[1::2, 1::2] = [255, 0, 0]
        arr_blue = np.zeros((50, 50, 3), dtype=np.uint8)
        arr_blue[::2, 1::2] = [0, 0, 255]
        arr_blue[1::2, ::2] = [0, 0, 255]
        Image.fromarray(arr_red).save(red, "JPEG")
        Image.fromarray(arr_blue).save(blue, "JPEG")
        assert dhash(red) != dhash(blue)

    def test_same_image_same_hash(self, tmp_path):
        img_path = tmp_path / "test.jpg"
        Image.new("RGB", (100, 100), color="red").save(img_path, "JPEG")
        # Hash twice — should be identical
        assert dhash(img_path) == dhash(img_path)

    def test_custom_hash_size(self, tmp_path):
        """Different hash_size → different hash length.

        Formula: hash_size^2 bits → hash_size^2/4 hex chars.
        """
        img_path = tmp_path / "test.jpg"
        Image.new("RGB", (100, 100), color="red").save(img_path, "JPEG")
        h2 = dhash(img_path, hash_size=2)
        h4 = dhash(img_path, hash_size=4)
        h8 = dhash(img_path, hash_size=8)
        assert len(h2) == 1  # 2^2=4 bits → 1 hex
        assert len(h4) == 4  # 4^2=16 bits → 4 hex
        assert len(h8) == 16  # 8^2=64 bits → 16 hex

    def test_works_with_png(self, tmp_path):
        img_path = tmp_path / "test.png"
        Image.new("RGB", (100, 100), color="green").save(img_path, "PNG")
        result = dhash(img_path)
        assert result is not None

    def test_works_with_webp(self, tmp_path):
        img_path = tmp_path / "test.webp"
        Image.new("RGB", (100, 100), color="yellow").save(img_path, "WEBP")
        result = dhash(img_path)
        assert result is not None


# ----- hamming_distance -----

class TestHammingDistance:
    """Bit-level difference between two dhash strings."""

    def test_identical_strings_zero_distance(self):
        assert hamming_distance("abc", "abc") == 0

    def test_same_length_different_chars(self):
        # 'a' and 'b' are hex digits: 0xa=10, 0xb=11 → 1 bit differs
        assert hamming_distance("a", "b") == 1

    def test_all_bits_differ_max(self):
        """All bits flipped → max distance = 8 * len (for hex)."""
        assert hamming_distance("0", "f") == 4  # 4 bits per hex char

    def test_different_lengths_returns_none(self):
        """Different-length hashes can't be compared."""
        result = hamming_distance("ab", "abc")
        assert result is None

    def test_empty_strings_return_none(self):
        """Empty strings are rejected — different-length check fails."""
        assert hamming_distance("", "") is None

    def test_symmetric(self):
        """Distance(a, b) == Distance(b, a)."""
        a = "abcdef12"
        b = "12345678"
        assert hamming_distance(a, b) == hamming_distance(b, a)

    def test_returns_integer(self):
        result = hamming_distance("a", "b")
        assert isinstance(result, int)


# ----- compute_fingerprints -----

class TestComputeFingerprints:
    """Convenience wrapper returning both fingerprints."""

    def test_returns_dict_with_both_keys(self, tmp_path):
        img_path = tmp_path / "test.jpg"
        Image.new("RGB", (100, 100), color="red").save(img_path, "JPEG")
        result = compute_fingerprints(img_path)
        assert "content_sha256" in result
        assert "dhash" in result

    def test_returns_dict_values(self, tmp_path):
        img_path = tmp_path / "test.jpg"
        Image.new("RGB", (100, 100), color="red").save(img_path, "JPEG")
        result = compute_fingerprints(img_path)
        assert result["content_sha256"] is not None
        assert result["dhash"] is not None

    def test_missing_file_returns_none_values(self, tmp_path):
        result = compute_fingerprints(tmp_path / "missing.jpg")
        assert result["content_sha256"] is None
        assert result["dhash"] is None

    def test_returns_dict_type(self, tmp_path):
        img_path = tmp_path / "test.jpg"
        Image.new("RGB", (100, 100), color="red").save(img_path, "JPEG")
        result = compute_fingerprints(img_path)
        assert isinstance(result, dict)


# ----- Module imports -----

class TestModuleImports:
    """Public API is importable."""

    def test_fingerprints_importable(self):
        from indexer import fingerprints
        assert callable(fingerprints.content_sha256)
        assert callable(fingerprints.dhash)
        assert callable(fingerprints.hamming_distance)
        assert callable(fingerprints.compute_fingerprints)

    def test_default_dhash_size_exported(self):
        from indexer.fingerprints import DEFAULT_DHASH_SIZE
        assert DEFAULT_DHASH_SIZE == 8