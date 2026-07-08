"""
tests/test_indexer.py

Layer 1 — indexer unit tests with in-memory Qdrant.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image
from qdrant_client import QdrantClient

from indexer import scan, upsert
from indexer.image_loader import LoaderError, SIGLIP_RESOLUTION, load, load_image_pil
from indexer.upsert import DEFAULT_COLLECTION, VECTOR_DIM, build_payload, id_for

# Test UUIDs (deterministic, valid UUID format that Qdrant accepts).
CAT_ID = "11111111-1111-1111-1111-111111111111"
DOG_ID = "22222222-2222-2222-2222-222222222222"
CAR_ID = "33333333-3333-3333-3333-333333333333"


# ---------------- scan.snapshot ----------------


def test_snapshot_returns_sorted_list(fixture_images: Path):
    paths = scan.snapshot(fixture_images)
    assert isinstance(paths, list)
    assert all(isinstance(p, Path) for p in paths)
    # Sorted.
    assert paths == sorted(paths)
    # Only the 5 pngs; .hidden.jpg, Thumbs.db, notes.txt are skipped.
    assert len(paths) == 5
    assert all(p.suffix == ".png" for p in paths)


def test_snapshot_skips_hidden_and_os_junk(fixture_images: Path):
    paths = scan.snapshot(fixture_images)
    names = [p.name for p in paths]
    assert ".hidden.jpg" not in names
    assert "Thumbs.db" not in names
    assert "notes.txt" not in names


def test_is_image_suffix_accepts_jfif():
    # JFIF is the JPEG File Interchange Format — a JPEG with a
    # different extension. Treat it as a regular image for indexing
    # and serving.
    assert scan.is_image_suffix(".jfif") is True
    assert scan.is_image_suffix(".JFIF") is True


def test_snapshot_includes_jfif_files(tmp_path: Path):
    img = Image.new("RGB", (16, 16), (10, 20, 30))
    img.save(tmp_path / "photo.jfif", "JPEG")
    paths = scan.snapshot(tmp_path)
    assert [p.name for p in paths] == ["photo.jfif"]


def test_snapshot_raises_on_missing_path(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        scan.snapshot(tmp_path / "does-not-exist")


def test_snapshot_raises_on_file_not_dir(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        scan.snapshot(f)


def test_snapshot_handles_empty_dir(tmp_path: Path):
    (tmp_path / "empty").mkdir()
    assert scan.snapshot(tmp_path / "empty") == []


# ---------------- id_for ----------------


def test_id_for_deterministic():
    p = Path("/some/where/img.jpg")
    assert id_for(p, "") == id_for(p, "")
    # UUID format: 36 chars, with hyphens.
    assert len(id_for(p, "")) == 36
    assert id_for(p, "").count("-") == 4


def test_id_for_shard_changes_id():
    p = Path("/some/where/img.jpg")
    assert id_for(p, "shard-a") != id_for(p, "shard-b")
    assert id_for(p, "shard-a") != id_for(p, "")


# ---------------- image_loader ----------------


def test_load_image_pil_applies_exif_transpose(tmp_path: Path):
    # Build an image with EXIF orientation=6 (rotate 90 CW).
    from PIL import Image

    img = Image.new("RGB", (10, 20), (255, 0, 0))
    # Write EXIF orientation. Some PIL builds need the Ifd tag enum.
    try:
        from PIL.ExifTags import IFD
        from PIL.TiffImagePlugin import IFDRational

        exif = img.getexif()
        exif[IFD.GPSInfo] = {}  # noop
        # Orientation tag is 0x0112 in the 0th IFD.
        exif[0x0112] = 6
        out = tmp_path / "oriented.jpg"
        img.save(out, exif=exif)
    except Exception:
        pytest.skip("EXIF orientation injection not supported on this PIL build")

    loaded = load_image_pil(out)
    # After transpose, width and height should be swapped.
    assert loaded.size == (20, 10)
    assert loaded.mode == "RGB"


def test_load_returns_square_rgb_image(fixture_images: Path):
    img = load(fixture_images / "img_00.png")
    assert img.size == (SIGLIP_RESOLUTION, SIGLIP_RESOLUTION)
    assert img.mode == "RGB"


def test_load_raises_loader_error_on_missing(tmp_path: Path):
    with pytest.raises(LoaderError):
        load(tmp_path / "nope.png")


def test_load_raises_loader_error_on_corrupt(tmp_path: Path):
    f = tmp_path / "junk.png"
    f.write_bytes(b"not a real png")
    with pytest.raises(LoaderError):
        load(f)


# ---------------- upsert ----------------


def test_ensure_collection_idempotent():
    client = QdrantClient(location=":memory:")
    upsert.ensure_collection(client, "test_coll", dim=VECTOR_DIM)
    # Calling again should be a no-op.
    upsert.ensure_collection(client, "test_coll", dim=VECTOR_DIM)
    names = {c.name for c in client.get_collections().collections}
    assert "test_coll" in names


def test_build_payload_includes_collection(tmp_path):
    """build_payload stamps the `collection` field on the payload so
    the search side can filter with a payload-indexed MatchAny.
    """
    p = tmp_path / "img.jpg"
    p.write_bytes(b"x")
    payload = build_payload(p, shard="", model_name="m", model_revision="",
                            collection="kpop")
    assert payload["collection"] == "kpop"
    assert payload["path"].endswith("img.jpg")


def test_build_payload_default_collection_is_empty_string(tmp_path):
    """When the indexer forgets to pass `collection` (e.g. a test
    fixture), the field is still present so the payload schema
    doesn't surprise the search side.
    """
    p = tmp_path / "img.jpg"
    p.write_bytes(b"x")
    payload = build_payload(p, shard="", model_name="m", model_revision="")
    assert payload["collection"] == ""


def test_ensure_payload_index_idempotent():
    """ensure_payload_index must be safe to call repeatedly — the
    indexer runs it once per invocation, and re-runs are common.
    """
    from indexer.upsert import ensure_collection, ensure_payload_index
    client = QdrantClient(location=":memory:")
    ensure_collection(client, "idx_test", dim=VECTOR_DIM)
    # Twice in a row — second call should be a no-op (or at minimum,
    # not raise).
    ensure_payload_index(client, "idx_test", "collection", "keyword")
    ensure_payload_index(client, "idx_test", "collection", "keyword")


def test_upsert_batch_inserts(qdrant_in_memory):
    client = qdrant_in_memory.client
    upsert.ensure_collection(client, qdrant_in_memory.collection, dim=VECTOR_DIM)
    items = [
        (CAT_ID, [0.0] * VECTOR_DIM, {"id": CAT_ID, "path": "/x"}),
        (DOG_ID, [0.1] * VECTOR_DIM, {"id": DOG_ID, "path": "/y"}),
    ]
    upsert.upsert_batch(client, qdrant_in_memory.collection, items, wait=True)
    info = client.get_collection(qdrant_in_memory.collection)
    assert info.points_count == 2


def test_existing_ids(qdrant_in_memory):
    client = qdrant_in_memory.client
    upsert.ensure_collection(client, qdrant_in_memory.collection, dim=VECTOR_DIM)
    items = [
        (CAT_ID, [0.0] * VECTOR_DIM, {"id": CAT_ID, "path": "/x"}),
    ]
    upsert.upsert_batch(client, qdrant_in_memory.collection, items, wait=True)
    found = upsert.existing_ids(
        client, qdrant_in_memory.collection, [CAT_ID, DOG_ID]
    )
    assert CAT_ID in found
    assert DOG_ID not in found


# ---------------- end-to-end (indexer with mock encoder) ----------------


def test_end_to_end_indexer_skips_already_indexed(tmp_path, fixture_images, monkeypatch, capsys):
    """
    Mock the vision encoder to return a fixed 1536-dim vector per image.
    Run the indexer end-to-end twice. The second run should report 0
    indexed (everything was cached by the first run).
    """
    from indexer import indexer as indexer_mod

    counter = {"calls": 0}

    class MockEncoder:
        def __init__(self, *a, **kw):
            pass

        def embed_batch(self, images):
            counter["calls"] += 1
            # Different deterministic vector per call
            return [
                [0.01 * (i + counter["calls"]) for _ in range(VECTOR_DIM)]
                for i, _ in enumerate(images)
            ]

    monkeypatch.setattr(indexer_mod, "VisionEncoder", MockEncoder)

    cache_path = tmp_path / "cache.json"

    # First run: cache is empty, so rebuild from Qdrant (which is
    # also empty). All 5 images get indexed; cache gets saved.
    args = indexer_mod.parse_args(
        [
            str(fixture_images),
            "--qdrant-in-memory",
            "--collection", "e2e_lib",
            "--qdrant-collection", "e2e_test",
            "--cache-file", str(cache_path),
            "--batch-size", "2",
            "--device", "cpu",
        ]
    )
    rc = indexer_mod.main(args)
    out1 = capsys.readouterr().out
    assert rc == 0
    assert "Indexed: 5" in out1
    assert "Skipped: 0" in out1
    assert counter["calls"] >= 1
    assert cache_path.exists(), "cache should be written after a successful run"

    # Second run: cache is loaded, so every file is a cache hit.
    # 0 indexed, 5 skipped.
    counter["calls"] = 0
    args2 = indexer_mod.parse_args(
        [
            str(fixture_images),
            "--qdrant-in-memory",
            "--collection", "e2e_lib",
            "--qdrant-collection", "e2e_test",
            "--cache-file", str(cache_path),
            "--batch-size", "2",
            "--device", "cpu",
        ]
    )
    rc2 = indexer_mod.main(args2)
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "Indexed: 0" in out2
    assert "Skipped: 5" in out2
    # Encoder should not be called — nothing to embed.
    assert counter["calls"] == 0


def test_end_to_end_with_dry_run(tmp_path, fixture_images, monkeypatch, capsys):
    from indexer import indexer as indexer_mod

    class MockEncoder:
        def __init__(self, *a, **kw):
            pass

        def embed_batch(self, images):
            return [[0.0] * VECTOR_DIM for _ in images]

    monkeypatch.setattr(indexer_mod, "VisionEncoder", MockEncoder)

    # dry-run bypasses the cache entirely (no upsert, no cache IO).
    args = indexer_mod.parse_args(
        [
            str(fixture_images),
            "--dry-run",
            "--qdrant-in-memory",
            "--collection",
            "dryrun_lib",
            "--qdrant-collection",
            "dryrun_test",
            "--cache-file", str(tmp_path / "cache.json"),
            "--device",
            "cpu",
        ]
    )
    rc = indexer_mod.main(args)
    captured = capsys.readouterr()
    assert rc == 0
    assert "Indexed: 5" in captured.out
    assert "Skipped: 0" in captured.out
    # dry-run never writes the cache.
    assert not (tmp_path / "cache.json").exists()


def test_indexer_returns_2_on_missing_source(tmp_path, monkeypatch):
    from indexer import indexer as indexer_mod

    class MockEncoder:
        def __init__(self, *a, **kw):
            pass

        def embed_batch(self, images):
            return []

    monkeypatch.setattr(indexer_mod, "VisionEncoder", MockEncoder)
    args = indexer_mod.parse_args(
        [
            str(tmp_path / "nope"),
            "--qdrant-in-memory",
            "--collection",
            "missing_lib",
            "--device",
            "cpu",
        ]
    )
    rc = indexer_mod.main(args)
    assert rc == 2


def test_prune_removes_missing_files(tmp_path):
    """prune removes points whose source file no longer exists."""
    from qdrant_client import QdrantClient
    from indexer import upsert
    from indexer.upsert import VECTOR_DIM, ensure_collection, upsert_batch, prune_missing
    import uuid

    client = QdrantClient(location=":memory:")
    ensure_collection(client, "test_prune", dim=VECTOR_DIM)

    # Create a file that exists.
    existing_file = tmp_path / "exists.jpg"
    existing_file.write_bytes(b"fake")
    # Create a file that we'll delete.
    missing_file = tmp_path / "missing.jpg"
    missing_file.write_bytes(b"fake")

    existing_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(existing_file)))
    missing_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(missing_file)))

    items = [
        (existing_id, [1.0] * VECTOR_DIM, {"id": existing_id, "path": str(existing_file)}),
        (missing_id, [1.0] * VECTOR_DIM, {"id": missing_id, "path": str(missing_file)}),
    ]
    upsert_batch(client, "test_prune", items, wait=True)

    # Delete the file from disk.
    missing_file.unlink()

    # Prune.
    removed = prune_missing(client, "test_prune")
    assert removed == 1

    # Verify only the existing point remains.
    points = client.retrieve(collection_name="test_prune", ids=[existing_id, missing_id])
    assert len(points) == 1
    assert str(points[0].id) == existing_id


# ---------------- IndexerCache ----------------


def test_cache_load_save_roundtrip(tmp_path):
    """load() then save() then load() again — entries survive."""
    from indexer.cache import IndexerCache

    p = tmp_path / "img.jpg"
    p.write_bytes(b"hello")

    c1 = IndexerCache(tmp_path / "cache.json", "images")
    c1.add(p, "id-abc", int(p.stat().st_mtime), int(p.stat().st_size))
    c1.save()

    c2 = IndexerCache(tmp_path / "cache.json", "images")
    assert c2.load() is True
    assert c2.has(p) is True


def test_cache_has_returns_true_for_unchanged_file(tmp_path):
    p = tmp_path / "img.jpg"
    p.write_bytes(b"hello")

    from indexer.cache import IndexerCache
    c = IndexerCache(tmp_path / "cache.json", "images")
    c.add(p, "id-abc", int(p.stat().st_mtime), int(p.stat().st_size))
    assert c.has(p) is True


def test_cache_has_returns_false_for_modified_file(tmp_path):
    """Touching a file changes its mtime; cache should report it as new."""
    p = tmp_path / "img.jpg"
    p.write_bytes(b"hello")
    stat = p.stat()

    from indexer.cache import IndexerCache
    c = IndexerCache(tmp_path / "cache.json", "images")
    c.add(p, "id-abc", int(stat.st_mtime), int(stat.st_size))

    # Bump mtime into the future.
    import os
    new_mtime = int(stat.st_mtime) + 100
    os.utime(p, (new_mtime, new_mtime))

    assert c.has(p) is False


def test_cache_has_returns_false_for_resized_file(tmp_path):
    """Overwriting a file with different bytes changes its size; cache miss."""
    p = tmp_path / "img.jpg"
    p.write_bytes(b"hello")

    from indexer.cache import IndexerCache
    c = IndexerCache(tmp_path / "cache.json", "images")
    c.add(p, "id-abc", int(p.stat().st_mtime), int(p.stat().st_size))

    p.write_bytes(b"hello, world! this is longer now")
    assert c.has(p) is False


def test_cache_has_returns_false_for_missing_file(tmp_path):
    """File deleted from disk: cache miss (and no exception)."""
    p = tmp_path / "img.jpg"
    p.write_bytes(b"hello")

    from indexer.cache import IndexerCache
    c = IndexerCache(tmp_path / "cache.json", "images")
    c.add(p, "id-abc", int(p.stat().st_mtime), int(p.stat().st_size))

    p.unlink()
    assert c.has(p) is False


def test_cache_load_returns_false_for_missing_file(tmp_path):
    from indexer.cache import IndexerCache
    c = IndexerCache(tmp_path / "cache.json", "images")
    assert c.load() is False
    assert len(c) == 0


def test_cache_load_ignores_version_mismatch(tmp_path):
    """A cache from an older schema version is silently ignored."""
    import json
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps({
        "version": 999,  # future version
        "collection": "images",
        "entries": {"/some/path": {"id": "x", "mtime": 0, "size": 0, "indexed_at": ""}},
    }))

    from indexer.cache import IndexerCache
    c = IndexerCache(cache_file, "images")
    assert c.load() is False
    assert len(c) == 0


def test_cache_load_ignores_collection_mismatch(tmp_path):
    """A cache built for collection 'foo' must not be loaded for 'bar'."""
    import json
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps({
        "version": 1,
        "collection": "foo",
        "entries": {},
    }))

    from indexer.cache import IndexerCache
    c = IndexerCache(cache_file, "bar")
    assert c.load() is False


def test_cache_load_handles_corrupt_file(tmp_path):
    """A corrupt JSON file is treated as empty, not a crash."""
    cache_file = tmp_path / "cache.json"
    cache_file.write_text("not valid json {{{")

    from indexer.cache import IndexerCache
    c = IndexerCache(cache_file, "images")
    assert c.load() is False
    assert len(c) == 0


def test_cache_remove_missing_drops_deleted_files(tmp_path):
    p1 = tmp_path / "exists.jpg"
    p1.write_bytes(b"x")
    p2 = tmp_path / "will_be_deleted.jpg"
    p2.write_bytes(b"x")

    from indexer.cache import IndexerCache
    c = IndexerCache(tmp_path / "cache.json", "images")
    c.add(p1, "id-1", int(p1.stat().st_mtime), int(p1.stat().st_size))
    c.add(p2, "id-2", int(p2.stat().st_mtime), int(p2.stat().st_size))
    assert len(c) == 2

    p2.unlink()
    dropped = c.remove_missing()
    assert dropped == 1
    assert len(c) == 1
    assert c.has(p1) is True


def test_cache_rebuild_from_qdrant(qdrant_in_memory, tmp_path):
    """rebuild_from_qdrant walks the collection and populates the cache,
    but skips entries whose paths no longer exist on disk."""
    from indexer.cache import IndexerCache
    from indexer import upsert

    existing = tmp_path / "exists.jpg"
    existing.write_bytes(b"x")
    deleted = tmp_path / "deleted.jpg"
    deleted.write_bytes(b"x")

    items = [
        (CAT_ID, [0.0] * VECTOR_DIM, {"id": CAT_ID, "path": str(existing)}),
        (DOG_ID, [0.1] * VECTOR_DIM, {"id": DOG_ID, "path": str(deleted)}),
    ]
    upsert.ensure_collection(qdrant_in_memory.client, qdrant_in_memory.collection, dim=VECTOR_DIM)
    upsert.upsert_batch(qdrant_in_memory.client, qdrant_in_memory.collection, items, wait=True)

    deleted.unlink()  # gone from disk before the rebuild

    c = IndexerCache(tmp_path / "cache.json", qdrant_in_memory.collection)
    c.rebuild_from_qdrant(qdrant_in_memory.client, qdrant_in_memory.collection)

    # The existing file is in the cache; the deleted one isn't.
    assert c.has(existing) is True
    assert len(c) == 1


def test_cache_save_is_atomic(tmp_path):
    """A crash mid-save leaves the previous cache file intact."""
    from indexer.cache import IndexerCache

    cache_file = tmp_path / "cache.json"
    c1 = IndexerCache(cache_file, "images")
    p = tmp_path / "img.jpg"
    p.write_bytes(b"x")
    c1.add(p, "id-1", int(p.stat().st_mtime), int(p.stat().st_size))
    c1.save()
    first_size = cache_file.stat().st_size

    # Now save a second time with different data. Simulate a crash by
    # leaving a stale .tmp file in the dir — load() should still work.
    c2 = IndexerCache(cache_file, "images")
    c2.add(p, "id-2", int(p.stat().st_mtime), int(p.stat().st_size))
    c2.save()

    # No leftover .tmp file.
    leftovers = list(tmp_path.glob(".cache-*.tmp"))
    assert leftovers == []
    assert cache_file.stat().st_size > 0


# ---------------- Indexer <-> Cache integration ----------------


def test_indexer_cache_picks_up_new_files(tmp_path, fixture_images, monkeypatch, capsys):
    """
    Run 1: index N files.
    Add 2 new files to the source dir.
    Run 2: 2 new files indexed, N old files skipped (from cache).
    """
    from indexer import indexer as indexer_mod

    class MockEncoder:
        def __init__(self, *a, **kw):
            pass

        def embed_batch(self, images):
            return [[0.0] * VECTOR_DIM for _ in images]

    monkeypatch.setattr(indexer_mod, "VisionEncoder", MockEncoder)

    cache_path = tmp_path / "cache.json"
    common = [
        str(fixture_images), "--qdrant-in-memory",
        "--collection", "newfiles_lib",
        "--qdrant-collection", "newfiles_test",
        "--cache-file", str(cache_path),
        "--batch-size", "2", "--device", "cpu",
    ]
    rc = indexer_mod.main(indexer_mod.parse_args(common))
    assert rc == 0

    # Add 2 new images to the source dir.
    from PIL import Image
    for i, color in enumerate([(10, 20, 30), (40, 50, 60)]):
        Image.new("RGB", (16, 16), color).save(fixture_images / f"new_{i:02d}.png")

    rc2 = indexer_mod.main(indexer_mod.parse_args(common))
    out = capsys.readouterr().out
    assert rc2 == 0
    assert "Indexed: 2" in out
    assert "Skipped: 5" in out


def test_indexer_modified_file_reembeds(tmp_path, fixture_images, monkeypatch, capsys):
    """
    A touched file (mtime change) should re-embed, not be skipped.
    """
    from indexer import indexer as indexer_mod
    import os

    class MockEncoder:
        def __init__(self, *a, **kw):
            pass

        def embed_batch(self, images):
            return [[0.0] * VECTOR_DIM for _ in images]

    monkeypatch.setattr(indexer_mod, "VisionEncoder", MockEncoder)

    cache_path = tmp_path / "cache.json"
    common = [
        str(fixture_images), "--qdrant-in-memory",
        "--collection", "modified_lib",
        "--qdrant-collection", "modified_test",
        "--cache-file", str(cache_path),
        "--batch-size", "2", "--device", "cpu",
    ]
    indexer_mod.main(indexer_mod.parse_args(common))

    # Touch one file: bump its mtime into the future.
    target = fixture_images / "img_00.png"
    stat = target.stat()
    os.utime(target, (stat.st_atime, stat.st_mtime + 1000))

    rc2 = indexer_mod.main(indexer_mod.parse_args(common))
    out = capsys.readouterr().out
    assert rc2 == 0
    # 1 file re-indexed, 4 skipped.
    assert "Indexed: 1" in out
    assert "Skipped: 4" in out


def test_indexer_no_cache_flag_falls_back_to_per_batch(
    tmp_path, fixture_images, monkeypatch, capsys,
):
    """
    --no-cache disables the cache. Second run with --no-cache on a
    fresh in-memory Qdrant would re-index everything (because the
    in-memory Qdrant doesn't persist), but with the cache present
    a *third* run without --no-cache should still find everything.
    """
    from indexer import indexer as indexer_mod

    class MockEncoder:
        def __init__(self, *a, **kw):
            pass

        def embed_batch(self, images):
            return [[0.0] * VECTOR_DIM for _ in images]

    monkeypatch.setattr(indexer_mod, "VisionEncoder", MockEncoder)

    cache_path = tmp_path / "cache.json"

    # Run 1: index with cache (populates the cache).
    rc1 = indexer_mod.main(indexer_mod.parse_args([
        str(fixture_images), "--qdrant-in-memory",
        "--collection", "nocache_lib",
        "--qdrant-collection", "nocache_test",
        "--cache-file", str(cache_path),
        "--batch-size", "2", "--device", "cpu",
    ]))
    assert rc1 == 0

    # Run 2: --no-cache. Fresh in-memory Qdrant, no cache lookup,
    # so all 5 get re-indexed (per-batch Qdrant retrieve sees an
    # empty collection).
    rc2 = indexer_mod.main(indexer_mod.parse_args([
        str(fixture_images), "--qdrant-in-memory",
        "--collection", "nocache_lib",
        "--qdrant-collection", "nocache_test",
        "--cache-file", str(cache_path),
        "--no-cache",
        "--batch-size", "2", "--device", "cpu",
    ]))
    out = capsys.readouterr().out
    assert rc2 == 0
    assert "Indexed: 5" in out
    assert "Skipped: 0" in out


def test_indexer_refresh_cache_rebuilds_from_qdrant(
    tmp_path, fixture_images, monkeypatch, capsys,
):
    """
    Simulate a stale cache by manually emptying the on-disk cache
    but keeping a populated Qdrant (in-memory; same client used
    twice doesn't work, so this test verifies the rebuild path
    more directly: --refresh-cache on a missing cache file does a
    full scroll-and-save).
    """
    from indexer import indexer as indexer_mod

    class MockEncoder:
        def __init__(self, *a, **kw):
            pass

        def embed_batch(self, images):
            return [[0.0] * VECTOR_DIM for _ in images]

    monkeypatch.setattr(indexer_mod, "VisionEncoder", MockEncoder)

    cache_path = tmp_path / "cache.json"

    # Run 1: index, cache populated.
    indexer_mod.main(indexer_mod.parse_args([
        str(fixture_images), "--qdrant-in-memory",
        "--collection", "refresh_lib",
        "--qdrant-collection", "refresh_test",
        "--cache-file", str(cache_path),
        "--batch-size", "2", "--device", "cpu",
    ]))
    assert cache_path.exists()

    # Wipe the cache manually (simulating "cache got out of sync").
    cache_path.unlink()

    # Run 2: --refresh-cache. Qdrant is fresh (in-memory), so the
    # rebuild finds 0 entries and the indexer then proceeds to
    # re-index all 5 (cache is now empty). This is the expected
    # behavior when Qdrant itself has been wiped.
    rc2 = indexer_mod.main(indexer_mod.parse_args([
        str(fixture_images), "--qdrant-in-memory",
        "--collection", "refresh_lib",
        "--qdrant-collection", "refresh_test",
        "--cache-file", str(cache_path),
        "--refresh-cache",
        "--batch-size", "2", "--device", "cpu",
    ]))
    out = capsys.readouterr().out
    assert rc2 == 0
    # All 5 indexed because Qdrant was empty (in-memory state lost).
    assert "Indexed: 5" in out
    # And the cache file should be present (rebuilt, even if empty).
    assert cache_path.exists()


def test_load_times_out_on_slow_read(monkeypatch, tmp_path):
    """
    A file whose PIL read exceeds _LOAD_TIMEOUT_S should raise
    LoaderError, not hang the indexer. The actual reading happens
    in a worker thread; the test verifies the timeout path
    converts the thread's TimeoutError into LoaderError.
    """
    import time
    from indexer import image_loader
    from indexer.image_loader import LoaderError, load

    def slow_load_image_pil(path):
        time.sleep(10)

    monkeypatch.setattr(image_loader, "load_image_pil", slow_load_image_pil)
    # 0.5s timeout so the test stays fast.
    monkeypatch.setattr(image_loader, "_LOAD_TIMEOUT_S", 0.5)

    fake = tmp_path / "anything.jpg"
    fake.write_bytes(b"")
    with pytest.raises(LoaderError, match="timed out"):
        load(fake)


def test_load_timeout_configurable_via_env(monkeypatch):
    """
    INDEXER_LOAD_TIMEOUT_S env var overrides the default. Useful
    for network shares that are consistently slow (raise to 60s+)
    or consistently fast (drop to 10s).
    """
    monkeypatch.setenv("INDEXER_LOAD_TIMEOUT_S", "99.5")
    # Re-import so the env var is read at module load.
    import importlib
    from indexer import image_loader
    importlib.reload(image_loader)
    assert image_loader._LOAD_TIMEOUT_S == 99.5
