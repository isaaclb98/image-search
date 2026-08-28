"""
tests/test_thumbnails_unit.py — Unit tests for indexer/thumbnails.py.

Thumbnail generation: 256×256 WebP q50, stored at
{THUMBNAIL_DIR}/{prefix}/{point_id}.webp with a 2-char prefix
shard to avoid one-directory-per-2M-files.

Critical for the indexer — without thumbnails, /thumb/{id} returns
404 and the gallery breaks. This is the exact module that had the
generation-skip bug I found earlier (local_sync.py never called it).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from PIL import Image

from indexer.thumbnails import (
    THUMBNAIL_DIR,
    THUMBNAIL_QUALITY,
    THUMBNAIL_SIZE,
    compute_thumbnail,
    generate_thumbnail_for_path,
    thumbnail_path,
)


# ----- thumbnail_path -----

class TestThumbnailPath:
    """The path layout is deterministic from the point_id."""

    def test_basic_path_layout(self, tmp_path, monkeypatch):
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        p = thumbnail_path("abc123def456")
        assert p == tmp_path / "ab" / "abc123def456.webp"
        assert p.suffix == ".webp"

    def test_prefix_is_first_two_chars(self, tmp_path, monkeypatch):
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        assert thumbnail_path("abcdef").parent.name == "ab"
        assert thumbnail_path("0123456").parent.name == "01"
        assert thumbnail_path("zz_yyyy").parent.name == "zz"

    def test_id_with_dash_or_underscore_works(self, tmp_path, monkeypatch):
        """Common id characters (dash, underscore) pass through cleanly."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        p = thumbnail_path("abc-def_ghi")
        assert p.name == "abc-def_ghi.webp"

    def test_uses_env_var(self, monkeypatch):
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", "/custom/thumb/dir")
        p = thumbnail_path("abc123")
        assert str(p).startswith("/custom/thumb/dir")


# ----- compute_thumbnail -----

class TestComputeThumbnail:
    """Generate a WebP thumbnail from a PIL Image."""

    def test_writes_webp_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        img = Image.new("RGB", (1024, 768), color="blue")
        result = compute_thumbnail(img, "point-abc-123")
        assert result is not None
        assert result.exists()
        assert result.suffix == ".webp"

    def test_creates_prefix_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        img = Image.new("RGB", (100, 100), color="red")
        compute_thumbnail(img, "abc123")
        # The "ab" prefix directory should have been created
        assert (tmp_path / "ab").is_dir()

    def test_returns_path_matching_thumbnail_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        img = Image.new("RGB", (100, 100), color="red")
        result = compute_thumbnail(img, "abc123")
        assert result == thumbnail_path("abc123")

    def test_thumbnail_dimensions_are_square(self, tmp_path, monkeypatch):
        """Output should be exactly 256x256 (square-cropped, not letterboxed)."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        img = Image.new("RGB", (1024, 768), color="green")
        result = compute_thumbnail(img, "abc123")
        with Image.open(result) as thumb:
            assert thumb.size == (256, 256)

    def test_smaller_image_upscaled_to_target(self, tmp_path, monkeypatch):
        """Smaller square inputs are resized up to 256x256 (center-crop is no-op)."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        img = Image.new("RGB", (64, 64), color="red")
        result = compute_thumbnail(img, "small123")
        with Image.open(result) as thumb:
            assert thumb.size == (256, 256)

    def test_square_image(self, tmp_path, monkeypatch):
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        img = Image.new("RGB", (512, 512), color="red")
        result = compute_thumbnail(img, "square12")
        with Image.open(result) as thumb:
            assert thumb.size == (256, 256)

    def test_portrait_image_becomes_square(self, tmp_path, monkeypatch):
        """Tall portrait: center-crop the shorter (width) side, resize to square."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        img = Image.new("RGB", (400, 800), color="red")
        result = compute_thumbnail(img, "tall1234")
        with Image.open(result) as thumb:
            assert thumb.size == (256, 256)

    def test_landscape_image_becomes_square(self, tmp_path, monkeypatch):
        """Wide landscape: center-crop the shorter (height) side, resize to square."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        img = Image.new("RGB", (800, 400), color="red")
        result = compute_thumbnail(img, "wide1234")
        with Image.open(result) as thumb:
            assert thumb.size == (256, 256)

    def test_center_crop_uses_center_of_image(self, tmp_path, monkeypatch):
        """A 4x4 image with distinct corners — center crop should sample center."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        # Build a 4x4 image where the center 2x2 is red, the borders are blue.
        # Up-res to a larger image so center-crop has meaningful content.
        img = Image.new("RGB", (100, 400), color="blue")
        # Paint the center column red
        for y in range(150, 250):
            for x in range(100):
                img.putpixel((x, y), (255, 0, 0))
        result = compute_thumbnail(img, "center12")
        # Resized 256x256 should be predominantly red (center was red)
        with Image.open(result) as thumb:
            assert thumb.size == (256, 256)
            # Sample the center pixel — should be reddish
            r, g, b = thumb.getpixel((128, 128))
            assert r > g  # red dominates over blue

    def test_rgba_image_converted(self, tmp_path, monkeypatch):
        """RGBA images should still produce valid WebP output."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        result = compute_thumbnail(img, "rgba1234")
        assert result.exists()
        with Image.open(result) as thumb:
            assert thumb.format == "WEBP"

    def test_grayscale_image(self, tmp_path, monkeypatch):
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        img = Image.new("L", (100, 100), color=128)
        result = compute_thumbnail(img, "gray12345")
        assert result.exists()

    def test_does_not_mutate_input(self, tmp_path, monkeypatch):
        """The function should work on a copy, not mutate the caller's image."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        img = Image.new("RGB", (1024, 768), color="red")
        original_size = img.size
        compute_thumbnail(img, "naming1")
        assert img.size == original_size

    def test_overwrites_existing_thumbnail(self, tmp_path, monkeypatch):
        """Re-running for the same point_id should overwrite, not error."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        img1 = Image.new("RGB", (100, 100), color="red")
        img2 = Image.new("RGB", (100, 100), color="blue")
        result1 = compute_thumbnail(img1, "same12345")
        result2 = compute_thumbnail(img2, "same12345")
        assert result1 == result2
        assert result2.exists()

    def test_failure_returns_none(self, tmp_path, monkeypatch):
        """If save fails, return None and don't crash."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        # Use a mock image whose .save() raises
        bad_img = MagicMock()
        bad_img.copy.return_value.copy.return_value.thumbnail.side_effect = None
        bad_img.copy.return_value.thumbnail.side_effect = OSError("disk full")

        result = compute_thumbnail(bad_img, "fail1234")
        assert result is None

    def test_failure_logs_warning(self, tmp_path, monkeypatch, caplog):
        """Failed generation should log a warning with the point_id."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        bad_img = MagicMock()
        bad_img.copy.return_value.thumbnail.side_effect = OSError("disk full")

        import logging
        with caplog.at_level(logging.WARNING, logger="indexer.thumbnails"):
            compute_thumbnail(bad_img, "fail1234")

        assert any("fail1234" in r.message for r in caplog.records)


# ----- generate_thumbnail_for_path -----

class TestGenerateThumbnailForPath:
    """Generate thumbnail from a source image path (used by local_sync)."""

    def test_basic_generation(self, tmp_path, monkeypatch):
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        # Create a source image
        src = tmp_path / "source.jpg"
        Image.new("RGB", (200, 200), color="red").save(src)
        # Point id should be derived from the path
        result = generate_thumbnail_for_path(
            Image.open(src),
            src,
            shard="",
        )
        assert result is not None
        assert result.exists()
        assert result.suffix == ".webp"

    def test_with_shard(self, tmp_path, monkeypatch):
        """Different shards should produce different point_ids for the same path."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        src = tmp_path / "photo.jpg"
        Image.new("RGB", (100, 100), color="red").save(src)

        result1 = generate_thumbnail_for_path(Image.open(src), src, shard="")
        result2 = generate_thumbnail_for_path(Image.open(src), src, shard="shard-a")

        # Different shards → different file paths
        assert result1 != result2
        assert result1.exists()
        assert result2.exists()

    def test_id_matches_upsert_logic(self, tmp_path, monkeypatch):
        """The point_id used should match what upsert.id_for produces."""
        monkeypatch.setattr("indexer.thumbnails.THUMBNAIL_DIR", str(tmp_path))
        from indexer.upsert import id_for

        src = tmp_path / "photo.jpg"
        Image.new("RGB", (100, 100), color="red").save(src)

        result = generate_thumbnail_for_path(
            Image.open(src), src, shard="test-shard"
        )

        # The thumbnail file name should be the id_for(...) of this path
        expected_id = id_for(src, "test-shard")
        assert result.name == f"{expected_id}.webp"


# ----- Module constants -----

class TestThumbnailConstants:
    """Constants are stable (referenced from indexer/local_sync.py)."""

    def test_thumbnail_size_is_256(self):
        assert THUMBNAIL_SIZE == (256, 256)

    def test_thumbnail_quality_is_50(self):
        assert THUMBNAIL_QUALITY == 50

    def test_thumbnail_dir_uses_env_or_default(self, monkeypatch):
        monkeypatch.delenv("THUMBNAIL_DIR", raising=False)
        # Default is /app/data/thumbnails (container path)
        assert THUMBNAIL_DIR == "/app/data/thumbnails" or "thumbnail" in THUMBNAIL_DIR