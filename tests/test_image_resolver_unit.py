"""
tests/test_image_resolver_unit.py — Unit tests for search/image_resolver.py.

Maps Qdrant payload paths to URLs the web UI can serve. Critical
because the indexer stores Windows paths but the search app runs on
Linux with a different mount.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from search.image_resolver import (
    resolve_url,
    resolve_local,
    guess_content_type,
)


# ----- resolve_url -----

class TestResolveUrl:
    """Build /photo/<id>/raw URLs from point_id + base."""

    def test_basic(self):
        assert resolve_url("abc-123", "http://localhost:8000") == (
            "http://localhost:8000/photo/abc-123/raw"
        )

    def test_strips_trailing_slash(self):
        assert resolve_url("abc", "http://x/") == "http://x/photo/abc/raw"

    def test_multiple_trailing_slashes_stripped(self):
        # rstrip("/") removes all trailing slashes
        assert resolve_url("abc", "http://x///") == "http://x/photo/abc/raw"

    def test_preserves_path_prefix(self):
        assert resolve_url("xyz", "http://example.com/app") == (
            "http://example.com/app/photo/xyz/raw"
        )

    def test_handles_https(self):
        assert resolve_url("id1", "https://example.com") == (
            "https://example.com/photo/id1/raw"
        )

    def test_handles_ip_with_port(self):
        assert resolve_url("id1", "http://192.168.1.10:8000") == (
            "http://192.168.1.10:8000/photo/id1/raw"
        )

    def test_id_with_uuid_format(self):
        url = resolve_url("f72ee119-fcf3-5db1-974b-a5f23ccb3682", "http://x")
        assert "f72ee119-fcf3-5db1-974b-a5f23ccb3682" in url

    def test_id_with_path_traversal_chars_still_works(self):
        """URL building is string interpolation — caller is responsible
        for sanitizing id. But the function shouldn't crash on unusual ids."""
        url = resolve_url("../etc/passwd", "http://x")
        assert url == "http://x/photo/../etc/passwd/raw"

    def test_empty_id(self):
        assert resolve_url("", "http://x") == "http://x/photo//raw"

    def test_id_with_special_chars(self):
        url = resolve_url("abc/def", "http://x")
        assert url == "http://x/photo/abc/def/raw"


# ----- resolve_local — same-machine paths -----

class TestResolveLocalSameMachine:
    """Payload paths that resolve on the same machine the indexer ran on."""

    def test_absolute_path_exists(self, tmp_path):
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")
        assert resolve_local(str(f), str(tmp_path)) == f

    def test_absolute_path_missing_returns_none(self, tmp_path):
        """Missing files return None — caller renders 'file not found'."""
        missing = tmp_path / "nope.jpg"
        assert resolve_local(str(missing), str(tmp_path)) is None

    def test_relative_path_joined_with_base(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        f = sub / "photo.jpg"
        f.write_bytes(b"x")
        # Relative payload "sub/photo.jpg" → tmp_path / "sub/photo.jpg"
        assert resolve_local("sub/photo.jpg", str(tmp_path)) == f

    def test_empty_payload_returns_none(self, tmp_path):
        assert resolve_local("", str(tmp_path)) is None

    def test_directory_returns_none(self, tmp_path):
        """Directories aren't files — is_file() check rejects them."""
        d = tmp_path / "subdir"
        d.mkdir()
        assert resolve_local(str(d), str(tmp_path)) is None

    def test_symlink_to_existing_file_resolves(self, tmp_path):
        target = tmp_path / "real.jpg"
        target.write_bytes(b"x")
        link = tmp_path / "link.jpg"
        link.symlink_to(target)
        assert resolve_local(str(link), str(tmp_path)) == link


# ----- resolve_local — cross-machine prefix mapping -----

class TestResolveLocalPrefixMapping:
    """Rewrite the indexer's mount path to the search app's mount path."""

    def test_windows_prefix_rewrites_to_linux_mount(self, tmp_path):
        """Z:\\images\\kpop\\photo.jpg → /mnt/nas/kpop/photo.jpg"""
        # Create the file at the resolved location
        nas = tmp_path / "nas"
        kpop = nas / "kpop"
        kpop.mkdir(parents=True)
        target = kpop / "photo.jpg"
        target.write_bytes(b"x")

        payload = "Z:\\images\\kpop\\photo.jpg"
        result = resolve_local(
            payload,
            base=str(nas),
            prefix="Z:\\images",
        )
        assert result == target

    def test_windows_prefix_with_forward_slashes(self, tmp_path):
        """Some Windows tools use Z:/images/... with forward slashes."""
        nas = tmp_path / "nas"
        kpop = nas / "kpop"
        kpop.mkdir(parents=True)
        target = kpop / "photo.jpg"
        target.write_bytes(b"x")

        payload = "Z:/images/kpop/photo.jpg"
        result = resolve_local(
            payload,
            base=str(nas),
            prefix="Z:/images",
        )
        assert result == target

    def test_posix_prefix_rewrites_to_linux_mount(self, tmp_path):
        """A POSIX prefix like /Volumes/nas also works."""
        nas = tmp_path / "mnt" / "nas"
        nas.mkdir(parents=True)
        target = nas / "photo.jpg"
        target.write_bytes(b"x")

        result = resolve_local(
            "/Volumes/nas/photo.jpg",
            base=str(nas),
            prefix="/Volumes/nas",
        )
        assert result == target

    def test_prefix_match_strips_leading_separator(self, tmp_path):
        """After replacing prefix, leading / or \\ doesn't double up."""
        nas = tmp_path / "nas"
        nas.mkdir(exist_ok=True)
        target = nas / "photo.jpg"
        target.write_bytes(b"x")

        # Payload has prefix + separator
        result = resolve_local(
            "Z:\\images\\photo.jpg",
            base=str(nas),
            prefix="Z:\\images",
        )
        # Should resolve to nas/photo.jpg, not nas//photo.jpg
        assert result == target
        assert "//" not in str(result)

    def test_prefix_mismatch_falls_through_to_as_is(self, tmp_path):
        """If prefix doesn't match, try the path as-is."""
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")
        # Prefix doesn't match this payload
        result = resolve_local(
            str(f),
            base=str(tmp_path),
            prefix="Z:\\different",
        )
        assert result == f

    def test_prefix_match_but_file_missing_returns_none(self, tmp_path):
        """Prefix matches but target doesn't exist on disk → None."""
        nas = tmp_path / "nas"
        nas.mkdir(exist_ok=True)  # empty
        result = resolve_local(
            "Z:\\images\\photo.jpg",
            base=str(nas),
            prefix="Z:\\images",
        )
        assert result is None

    def test_empty_prefix_skips_rewriting(self, tmp_path):
        """prefix="" (default) means no rewriting — use path as-is."""
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")
        result = resolve_local(str(f), base=str(tmp_path), prefix="")
        assert result == f

    def test_prefix_match_with_backslashes_normalized(self, tmp_path):
        """Backslashes in the relative part get normalized to forward slashes."""
        nas = tmp_path / "nas"
        sub = nas / "sub" / "dir"
        sub.mkdir(parents=True)
        target = sub / "photo.jpg"
        target.write_bytes(b"x")

        result = resolve_local(
            "Z:\\images\\sub\\dir\\photo.jpg",
            base=str(nas),
            prefix="Z:\\images",
        )
        assert result == target


# ----- resolve_local — error handling -----

class TestResolveLocalErrors:
    """resolve_local should never crash, even on weird inputs."""

    def test_payload_with_only_prefix_returns_none_if_no_file(self, tmp_path):
        """Edge case: payload is exactly the prefix string."""
        nas = tmp_path / "nas"
        nas.mkdir(exist_ok=True)
        result = resolve_local(
            "Z:\\images",
            base=str(nas),
            prefix="Z:\\images",
        )
        assert result is None

    def test_os_error_during_check_returns_none(self, tmp_path, monkeypatch):
        """If is_file() raises OSError, return None gracefully."""
        from pathlib import Path

        def raising_exists(self):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "exists", raising_exists)
        # Should not crash
        result = resolve_local("/some/path.jpg", base="/tmp")
        assert result is None


# ----- guess_content_type -----

class TestGuessContentType:
    """MIME type detection for served images."""

    @pytest.mark.parametrize("filename,expected_ctype", [
        ("photo.jpg", "image/jpeg"),
        ("photo.jpeg", "image/jpeg"),
        ("photo.JPG", "image/jpeg"),  # case-insensitive
        ("photo.png", "image/png"),
        ("photo.webp", "image/webp"),
        ("photo.gif", "image/gif"),
        ("photo.heic", "image/heic"),
        ("photo.jfif", "image/jpeg"),
    ])
    def test_known_image_types(self, filename, expected_ctype):
        assert guess_content_type(Path(filename)) == expected_ctype

    def test_unknown_extension_falls_back_to_octet_stream(self):
        assert guess_content_type(Path("file.qqq")) == "application/octet-stream"

    def test_no_extension_falls_back_to_octet_stream(self):
        assert guess_content_type(Path("Makefile")) == "application/octet-stream"

    def test_directory_path_falls_back_to_octet_stream(self):
        """guess_type('foo') for a directory with no extension."""
        assert guess_content_type(Path("/tmp")) == "application/octet-stream"

    def test_jfif_extension_works(self):
        """JFIF is a JPEG variant — explicit mapping in fallback dict."""
        assert guess_content_type(Path("photo.jfif")) == "image/jpeg"

    def test_mixed_case_extension(self):
        """Extension match is case-insensitive."""
        assert guess_content_type(Path("photo.JPEG")) == "image/jpeg"
        assert guess_content_type(Path("photo.WebP")) == "image/webp"


# ----- Integration: resolve_url + resolve_local together -----

class TestResolveIntegration:
    """resolve_url builds the web URL; resolve_local maps the payload."""

    def test_resolve_local_then_url(self, tmp_path):
        """End-to-end: indexer payload → local path → web URL."""
        # Create a real file at the resolved location
        target = tmp_path / "photo.jpg"
        target.write_bytes(b"x")

        # Indexer payload uses Windows path
        payload = "Z:\\images\\photo.jpg"
        local = resolve_local(payload, base=str(tmp_path), prefix="Z:\\images")
        assert local == target

        # Search app builds the URL from the point_id
        url = resolve_url("point-123", "http://localhost:8000")
        assert url == "http://localhost:8000/photo/point-123/raw"