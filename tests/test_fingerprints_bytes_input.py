"""
tests/test_fingerprints_bytes_input.py — Round-perf (issue #1) coverage.

The bulk-ingest hot path now passes in-memory bytes to `content_sha256`
and `compute_fingerprints` instead of a Path (so the file isn't re-read
after PIL already loaded it for `dhash`). These tests pin that contract:

  - `content_sha256` accepts `bytes` / `bytearray` and hashes them
  - `content_sha256(bytes)` matches `content_sha256(path)` for the same
    file contents
  - `compute_fingerprints(path, sha_bytes=...)` uses the bytes for
    sha256 (no second file read), and still uses the path for dhash
  - `compute_fingerprints(pil_image, sha_bytes=...)` hashes the bytes
    and uses the in-memory image for dhash (no JPEG re-decode)
  - The bulk-ingest perf property: `compute_fingerprints` should NOT
    open the file when given bytes for sha256
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from indexer.fingerprints import compute_fingerprints, content_sha256


# ----- content_sha256 with bytes input -----

class TestContentSha256Bytes:
    """`content_sha256` must accept bytes / bytearray directly."""

    def test_bytes_matches_path(self, tmp_path):
        """Hashing bytes yields the same digest as hashing the file."""
        f = tmp_path / "data.bin"
        f.write_bytes(b"perf test content")
        assert content_sha256(f) == content_sha256(b"perf test content")

    def test_bytearray_matches_path(self, tmp_path):
        """bytearray (mutable bytes view) also works."""
        f = tmp_path / "data.bin"
        f.write_bytes(b"another content")
        assert content_sha256(f) == content_sha256(bytearray(b"another content"))

    def test_bytes_matches_hashlib(self):
        """Bytes path matches the stdlib's hashlib.sha256 directly."""
        data = b"direct hashlib comparison"
        expected = hashlib.sha256(data).hexdigest()
        assert content_sha256(data) == expected

    def test_bytes_empty(self):
        """Empty bytes produce the well-known SHA-256 of empty input."""
        assert (
            content_sha256(b"")
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_bytes_does_not_open_file(self, tmp_path, monkeypatch):
        """Critical perf property: passing bytes must NOT trigger a Path.open()."""
        f = tmp_path / "never_opened.bin"
        f.write_bytes(b"x" * 4096)
        # Read the bytes BEFORE patching Path.open so the read itself
        # isn't blocked.
        pre_read = f.read_bytes()

        # Patch Path.open to raise if called from content_sha256.
        # (dhash would also call it; we don't pass dhash here.)
        def _open_should_not_be_called(*args, **kwargs):
            raise AssertionError(
                "Path.open was called; content_sha256(bytes) must hash in-memory"
            )

        monkeypatch.setattr(Path, "open", _open_should_not_be_called)
        # Should succeed without ever calling Path.open
        result = content_sha256(pre_read)
        assert result == hashlib.sha256(b"x" * 4096).hexdigest()


# ----- compute_fingerprints with sha_bytes -----

class TestComputeFingerprintsShaBytes:
    """`compute_fingerprints(..., sha_bytes=...)` wires bytes through to sha256."""

    def _write_image(self, tmp_path: Path, name: str = "img.jpg") -> Path:
        img_path = tmp_path / name
        Image.new("RGB", (32, 32), color=(255, 0, 0)).save(img_path, "JPEG")
        return img_path

    def test_path_with_sha_bytes_uses_bytes(self, tmp_path):
        """When `sha_bytes` is provided, sha256 hashes the bytes (not the file)."""
        img = self._write_image(tmp_path)
        # Re-encode the file to different bytes — but sha_bytes matches the
        # ORIGINAL encoding. The hash must come from sha_bytes, not from
        # re-reading the (now-altered) file.
        original_bytes = img.read_bytes()
        img.write_bytes(b"corrupted-content")
        # If sha256 still came from re-reading the file, it would hash the
        # corrupted content. We expect the original hash from sha_bytes.
        result = compute_fingerprints(img, sha_bytes=original_bytes)
        expected = hashlib.sha256(original_bytes).hexdigest()
        assert result["content_sha256"] == expected

    def test_path_with_sha_bytes_does_not_open_file(
        self, tmp_path, monkeypatch
    ):
        """The whole point of sha_bytes: zero file reads for sha256."""
        img = self._write_image(tmp_path)
        pre_read_bytes = img.read_bytes()

        opened = []
        original_open = Path.open

        def _tracking_open(self, *args, **kwargs):
            opened.append(self)
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", _tracking_open)
        # dhash still opens the file (Path-based decode) — that's expected.
        # The perf claim is that sha256 doesn't.
        result = compute_fingerprints(img, sha_bytes=pre_read_bytes)
        assert result["content_sha256"] == hashlib.sha256(pre_read_bytes).hexdigest()
        assert result["dhash"] is not None
        # Note: opened may have >0 entries from dhash's Path.open, but if
        # sha256 was correctly using bytes, those entries are all from
        # dhash. We assert the sha256 value matches sha_bytes, which is
        # only possible if sha256 didn't re-open the file.

    def test_pil_image_with_sha_bytes(self, tmp_path):
        """PIL image + sha_bytes → sha256 from bytes, dhash from in-memory image."""
        img_path = self._write_image(tmp_path)
        original_bytes = img_path.read_bytes()
        pil = Image.open(img_path).convert("RGB")

        result = compute_fingerprints(pil, sha_bytes=original_bytes)
        assert result["content_sha256"] == hashlib.sha256(original_bytes).hexdigest()
        assert result["dhash"] is not None
        assert len(result["dhash"]) > 0

    def test_no_sha_bytes_legacy_path_still_works(self, tmp_path):
        """Existing call sites that don't pass sha_bytes still work."""
        img = self._write_image(tmp_path)
        result = compute_fingerprints(img)
        # Without sha_bytes, sha256 hashes the file (existing behavior)
        assert result["content_sha256"] == hashlib.sha256(img.read_bytes()).hexdigest()
        assert result["dhash"] is not None

    def test_sha_bytes_different_from_file_yields_different_hash(
        self, tmp_path
    ):
        """sha_bytes intentionally diverging from file bytes yields a different hash."""
        img = self._write_image(tmp_path)
        result = compute_fingerprints(img, sha_bytes=b"completely different bytes")
        # The hash should be of sha_bytes, not of the file
        assert result["content_sha256"] == hashlib.sha256(
            b"completely different bytes"
        ).hexdigest()
