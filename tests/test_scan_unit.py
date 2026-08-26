"""
tests/test_scan_unit.py — Unit tests for indexer/scan.py.

Folder walking for the indexer. Critical invariant: returns a list
snapshot, not a generator (so mid-iteration renames don't break us).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from indexer.scan import (
    IMAGE_EXTENSIONS,
    SKIP_NAMES,
    _format_eta,
    is_image,
    is_image_suffix,
    should_skip,
    should_skip_name,
    snapshot,
)


# ----- IMAGE_EXTENSIONS constant -----

class TestImageExtensions:
    """The set of file extensions we know how to embed."""

    def test_common_extensions(self):
        assert ".jpg" in IMAGE_EXTENSIONS
        assert ".jpeg" in IMAGE_EXTENSIONS
        assert ".png" in IMAGE_EXTENSIONS
        assert ".webp" in IMAGE_EXTENSIONS

    def test_modern_formats(self):
        assert ".heic" in IMAGE_EXTENSIONS
        assert ".heif" in IMAGE_EXTENSIONS

    def test_legacy_format(self):
        assert ".jfif" in IMAGE_EXTENSIONS

    def test_non_image_extensions_excluded(self):
        assert ".txt" not in IMAGE_EXTENSIONS
        assert ".json" not in IMAGE_EXTENSIONS
        assert ".py" not in IMAGE_EXTENSIONS


# ----- SKIP_NAMES constant -----

class TestSkipNames:
    """OS metadata files that should never be embedded."""

    def test_thumbs_db(self):
        assert "thumbs.db" in SKIP_NAMES

    def test_ds_store(self):
        assert ".ds_store" in SKIP_NAMES


# ----- is_image_suffix -----

class TestIsImageSuffix:
    """True if a suffix is a known image extension."""

    @pytest.mark.parametrize("suffix", [
        ".jpg", ".JPG", ".jpeg", ".png", ".webp",
        ".heic", ".heif", ".jfif",
    ])
    def test_known_image_suffixes(self, suffix):
        assert is_image_suffix(suffix) is True

    @pytest.mark.parametrize("suffix", [
        ".txt", ".json", ".py", ".md", ".exe",
    ])
    def test_unknown_suffixes(self, suffix):
        assert is_image_suffix(suffix) is False

    def test_empty_string(self):
        assert is_image_suffix("") is False


# ----- should_skip_name -----

class TestShouldSkipName:
    """True if a filename is junk (hidden, OS metadata)."""

    def test_hidden_file(self):
        """Dot-prefixed files are hidden → skip."""
        assert should_skip_name(".hidden") is True

    def test_normal_file(self):
        assert should_skip_name("photo.jpg") is False

    def test_thumbs_db_is_skipped(self):
        """Thumbs.db (any case) is OS metadata → skipped."""
        assert should_skip_name("Thumbs.db") is True
        assert should_skip_name("thumbs.db") is True

    def test_ds_store(self):
        assert should_skip_name(".DS_Store") is True  # starts with .

    def test_empty_string(self):
        """Empty string doesn't start with '.', so not skipped."""
        assert should_skip_name("") is False


# ----- is_image -----

class TestIsImage:
    """True if a path has a known image suffix."""

    def test_jpg(self, tmp_path):
        assert is_image(tmp_path / "photo.jpg") is True

    def test_png(self, tmp_path):
        assert is_image(tmp_path / "photo.png") is True

    def test_non_image(self, tmp_path):
        assert is_image(tmp_path / "file.txt") is False

    def test_no_extension(self, tmp_path):
        assert is_image(tmp_path / "noext") is False


# ----- should_skip -----

class TestShouldSkip:
    """True if a path should be skipped (hidden, OS metadata, or non-image)."""

    def test_normal_image_not_skipped(self, tmp_path):
        assert should_skip(tmp_path / "photo.jpg") is False

    def test_hidden_file_skipped(self, tmp_path):
        assert should_skip(tmp_path / ".hidden.jpg") is True

    def test_non_image_is_not_skipped(self, tmp_path):
        """should_skip is about hidden/OS metadata, not file type.

        Non-image files are filtered by is_image() elsewhere, not
        by should_skip().
        """
        assert should_skip(tmp_path / "doc.txt") is False


# ----- _format_eta -----

class TestFormatEta:
    """Format seconds-remaining as a human-readable ETA string."""

    def test_zero_seconds(self):
        """Zero seconds → some short string."""
        result = _format_eta(0)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_seconds(self):
        """Sub-minute: just seconds."""
        result = _format_eta(30)
        assert isinstance(result, str)
        # Usually contains "s" or "sec"
        assert any(c in result.lower() for c in ["s", "sec"])

    def test_minutes(self):
        """Minutes-level ETA."""
        result = _format_eta(150)  # 2.5 minutes
        assert isinstance(result, str)
        # Usually contains "m" or "min"
        assert any(c in result.lower() for c in ["m", "min"])

    def test_hours(self):
        """Hours-level ETA."""
        result = _format_eta(7200)  # 2 hours
        assert isinstance(result, str)
        # Usually contains "h" or "hr"
        assert any(c in result.lower() for c in ["h", "hr"])

    def test_returns_string(self):
        assert isinstance(_format_eta(100), str)


# ----- snapshot -----

class TestSnapshot:
    """Walk a directory and return a list of image paths."""

    def test_empty_directory(self, tmp_path):
        result = snapshot(tmp_path)
        assert result == []

    def test_returns_list_not_generator(self, tmp_path):
        """Critical invariant: snapshot returns a list, not a generator."""
        # Create one image so the walker has something to find
        Image.new("RGB", (10, 10), color="red").save(tmp_path / "test.jpg")
        result = snapshot(tmp_path)
        # Must be a list (materialized), not a generator
        assert isinstance(result, list)

    def test_finds_images(self, tmp_path):
        Image.new("RGB", (10, 10), color="red").save(tmp_path / "a.jpg")
        Image.new("RGB", (10, 10), color="blue").save(tmp_path / "b.png")
        result = snapshot(tmp_path)
        assert len(result) == 2
        names = {p.name for p in result}
        assert "a.jpg" in names
        assert "b.png" in names

    def test_skips_non_images(self, tmp_path):
        Image.new("RGB", (10, 10), color="red").save(tmp_path / "img.jpg")
        (tmp_path / "doc.txt").write_text("not an image")
        result = snapshot(tmp_path)
        assert len(result) == 1
        assert result[0].name == "img.jpg"

    def test_skips_hidden_files(self, tmp_path):
        (tmp_path / ".hidden.jpg").write_bytes(b"fake")
        Image.new("RGB", (10, 10), color="red").save(tmp_path / "visible.jpg")
        result = snapshot(tmp_path)
        names = {p.name for p in result}
        assert "visible.jpg" in names
        assert ".hidden.jpg" not in names

    def test_recursive_into_subdirectories(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        Image.new("RGB", (10, 10), color="red").save(sub / "nested.jpg")
        result = snapshot(tmp_path)
        assert any("nested.jpg" in str(p) for p in result)

    def test_nonexistent_directory_raises(self, tmp_path):
        """Non-existent dir raises FileNotFoundError (caller checks first)."""
        with pytest.raises(FileNotFoundError):
            snapshot(tmp_path / "does-not-exist")


# ----- Module imports -----

class TestModuleImports:
    """Public API is importable."""

    def test_scan_importable(self):
        from indexer import scan
        assert callable(scan.snapshot)
        assert callable(scan.is_image)
        assert callable(scan.should_skip)

    def test_constants_exported(self):
        from indexer.scan import IMAGE_EXTENSIONS, SKIP_NAMES
        assert isinstance(IMAGE_EXTENSIONS, frozenset)
        assert isinstance(SKIP_NAMES, frozenset)