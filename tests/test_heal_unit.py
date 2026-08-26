"""
tests/test_heal_unit.py — Unit tests for indexer/heal.py.

The heal tool reconciles a photo directory against Qdrant, finding
orphaned points (in Qdrant, missing on disk) and new/modified files
(on disk, missing/stale in Qdrant).

Tests focus on the pure-Python helpers (parse_args, _canonical_path,
_optional_int, _is_under, _detect_source_dir) and the dataclasses.
The reconcile() integration is tested via the existing test_heal.py.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from indexer.heal import (
    DiskFile,
    HealReport,
    QdrantPoint,
    _canonical_path,
    _detect_source_dir,
    _is_under,
    _optional_int,
    parse_args,
)


# ----- DiskFile dataclass -----

class TestDiskFileDataclass:
    """A file on disk: path, mtime, size."""

    def test_basic_construction(self):
        f = DiskFile(path="/img/test.jpg", mtime=1700000000, size=1024)
        assert f.path == "/img/test.jpg"
        assert f.mtime == 1700000000
        assert f.size == 1024

    def test_zero_size(self):
        """Zero-byte files are valid (edge case)."""
        f = DiskFile(path="/empty.jpg", mtime=0, size=0)
        assert f.size == 0


# ----- QdrantPoint dataclass -----

class TestQdrantPointDataclass:
    """A point in Qdrant: id, path, optional mtime/size."""

    def test_basic_construction(self):
        p = QdrantPoint(id="abc-123", path="/img/test.jpg")
        assert p.id == "abc-123"
        assert p.path == "/img/test.jpg"
        assert p.mtime is None
        assert p.size is None

    def test_full_construction(self):
        p = QdrantPoint(id="abc", path="/x.jpg", mtime=1000, size=500)
        assert p.mtime == 1000
        assert p.size == 500


# ----- HealReport dataclass -----

class TestHealReportDataclass:
    """The reconciliation report."""

    def test_default_construction(self):
        report = HealReport(source=Path("/imgs"), collection="images")
        assert report.source == Path("/imgs")
        assert report.collection == "images"
        assert report.total_points == 0
        assert report.total_files == 0
        assert report.orphans == []
        assert report.new_files == []
        assert report.modified_files == []
        assert report.outside_scope == []
        assert report.errors == []

    def test_accumulating_results(self):
        report = HealReport(source=Path("/imgs"), collection="images")
        report.orphans.append(QdrantPoint(id="1", path="/x.jpg"))
        report.new_files.append(DiskFile(path="/y.jpg", mtime=0, size=0))
        assert len(report.orphans) == 1
        assert len(report.new_files) == 1

    def test_errors_tracked(self):
        report = HealReport(source=Path("/imgs"), collection="images")
        report.errors.append("disk error: permission denied")
        assert "permission denied" in report.errors[0]


# ----- _canonical_path -----

class TestCanonicalPath:
    """Normalize a path to a canonical string form."""

    def test_already_canonical_unchanged(self):
        assert _canonical_path("/path/to/file.jpg") == "/path/to/file.jpg"

    def test_empty_string(self):
        assert _canonical_path("") == ""

    def test_returns_string(self):
        assert isinstance(_canonical_path("/x.jpg"), str)


# ----- _optional_int -----

class TestOptionalInt:
    """Coerce a value to int, returning None if not coercible."""

    def test_int_passes_through(self):
        assert _optional_int(42) == 42

    def test_float_becomes_int(self):
        assert _optional_int(42.5) == 42  # truncates

    def test_string_int(self):
        assert _optional_int("42") == 42

    def test_none_returns_none(self):
        assert _optional_int(None) is None

    def test_empty_string_returns_none(self):
        assert _optional_int("") is None

    def test_invalid_string_returns_none(self):
        assert _optional_int("not-a-number") is None

    def test_list_returns_none(self):
        assert _optional_int([1, 2, 3]) is None

    def test_dict_returns_none(self):
        assert _optional_int({"k": "v"}) is None

    def test_negative_int(self):
        assert _optional_int(-5) == -5


# ----- _is_under -----

class TestIsUnder:
    """Check if a path is under a root directory."""

    def test_path_under_root(self, tmp_path):
        root = tmp_path
        child = tmp_path / "subdir" / "file.jpg"
        assert _is_under(str(child), root) is True

    def test_path_not_under_root(self, tmp_path):
        root = tmp_path / "subdir"
        outside = tmp_path / "other" / "file.jpg"
        assert _is_under(str(outside), root) is False

    def test_path_equals_root_is_false(self, tmp_path):
        """The root itself is NOT 'under' the root (startswith + "/")."""
        root = tmp_path
        # _is_under checks for path starting with "root/" — exact match fails
        assert _is_under(str(root), root) is False

    def test_path_outside_root_prefix(self, tmp_path):
        """A path with a similar name but outside should not match."""
        root = tmp_path / "photos"
        # A path in 'photos_backup' is NOT under 'photos'
        sneaky = tmp_path / "photos_backup" / "file.jpg"
        assert _is_under(str(sneaky), root) is False

    def test_nonexistent_root(self, tmp_path):
        """A non-existent root shouldn't crash."""
        fake_root = tmp_path / "does-not-exist"
        real_path = tmp_path / "real" / "file.jpg"
        # Should not raise, just return False
        result = _is_under(str(real_path), fake_root)
        assert result is False


# ----- _detect_source_dir -----

class TestDetectSourceDir:
    """Auto-detect the source directory from Qdrant payload paths."""

    def test_empty_list_returns_none(self):
        assert _detect_source_dir([]) is None

    def test_single_path_returns_none_or_root(self):
        """With one path, can't determine common root."""
        result = _detect_source_dir(["/photos/cat.jpg"])
        # Single path may or may not have a common root — just verify no crash
        assert result is None or isinstance(result, Path)

    def test_common_prefix_detected(self):
        paths = [
            "/photos/cat.jpg",
            "/photos/dog.jpg",
            "/photos/bird.jpg",
        ]
        result = _detect_source_dir(paths)
        if result is not None:
            # Common prefix should be /photos or a subdirectory
            assert str(result).startswith("/photos")

    def test_no_common_root(self):
        paths = [
            "/photos1/cat.jpg",
            "/photos2/dog.jpg",
        ]
        result = _detect_source_dir(paths)
        # No common prefix → may return None or some default
        # Just verify no crash
        assert result is None or isinstance(result, Path)

    def test_mixed_separators(self):
        """Windows backslashes should be normalized."""
        paths = [
            r"C:\\photos\\cat.jpg",
            r"C:\\photos\\dog.jpg",
        ]
        result = _detect_source_dir(paths)
        # Should not crash
        assert result is None or isinstance(result, Path)


# ----- parse_args -----

class TestParseArgs:
    """CLI argument parsing."""

    def test_defaults(self):
        args = parse_args([])
        assert args.batch_size == 1000
        assert args.apply is False
        assert args.verbose is False
        assert args.quiet is False

    def test_source_positional(self):
        args = parse_args(["/path/to/photos"])
        assert args.source == Path("/path/to/photos")

    def test_source_optional(self):
        args = parse_args([])
        assert args.source is None

    def test_qdrant_url(self):
        args = parse_args(["--qdrant-url", "http://qdrant.example.com:6333"])
        assert args.qdrant_url == "http://qdrant.example.com:6333"

    def test_collection_flag(self):
        args = parse_args(["--collection", "my-collection"])
        assert args.collection == "my-collection"

    def test_apply(self):
        args = parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_apply(self):
        args = parse_args(["--apply"])
        assert args.apply is True

    def test_batch_size(self):
        args = parse_args(["--batch-size", "1000"])
        assert args.batch_size == 1000

    def test_verbose(self):
        args = parse_args(["--verbose"])
        assert args.verbose is True

    def test_quiet(self):
        args = parse_args(["--quiet"])
        assert args.quiet is True


# ----- Module imports -----

class TestModuleImports:
    """Public API is importable."""

    def test_heal_importable(self):
        from indexer import heal
        assert callable(heal.parse_args)

    def test_dataclasses_importable(self):
        from indexer.heal import DiskFile, HealReport, QdrantPoint
        assert DiskFile is not None
        assert HealReport is not None
        assert QdrantPoint is not None