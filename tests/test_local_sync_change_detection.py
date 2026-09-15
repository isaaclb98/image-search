"""
tests/test_local_sync_change_detection.py

Layer 1 tests for local_sync change detection (mtime/size-based re-embed).

Guards:
- unchanged files are skipped (no re-embed, no second point)
- files whose mtime/size changed are re-embedded INTO the same
  deterministic point id (favourites/album membership survives)
- legacy points without stored mtime/size are treated as changed,
  so they heal on the next sync
- dry-run reports what would be embedded without touching the collection
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from indexer import local_sync as local_sync_mod
from indexer.upsert import id_for

COLLECTION = "images_test_changedetect"


def _make_png(tmp_path: Path, name: str, color=(10, 90, 200)) -> Path:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        pytest.skip("Pillow not available")
    p = tmp_path / name
    Image.new("RGB", (16, 16), color).save(p)
    return p


def _run(monkeypatch, raw, source_dir: Path, extra_args=None) -> int:
    monkeypatch.setattr(local_sync_mod, "make_client", lambda _: raw)
    args = [
        "--source", str(source_dir), "--source-name", "x",
        "--qdrant-collection", COLLECTION,
        "--device", "cpu",
        # The real default model (ViT-gopt-16-SigLIP2-384) is multi-GB
        # and slow on CPU; the conftest registers `mock-1536` in the
        # registry, and local_sync looks it up by name via VisionEncoder.
        # The change-detection invariant (point id + payload schema
        # shape) is identical regardless of which embedder is used.
        "--model", "mock-1536",
    ]
    if extra_args:
        args.extend(extra_args)
    return local_sync_mod.main(args)


def _points(raw) -> list:
    if not raw.collection_exists(COLLECTION):
        return []
    pts, _ = raw.scroll(
        collection_name=COLLECTION, limit=50,
        with_payload=True, with_vectors=True,
    )
    return pts


def _initial_sync(monkeypatch, raw, src: Path) -> None:
    rc = _run(monkeypatch, raw, src)
    assert rc == 0


def test_unchanged_file_not_reembedded(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    _make_png(src, "a.png")

    _initial_sync(monkeypatch, raw, src)
    pts = _points(raw)
    assert len(pts) == 1
    first_id = str(pts[0].id)
    first_mtime = pts[0].payload["mtime"]

    rc = _run(monkeypatch, raw, src)
    assert rc == 0

    pts = _points(raw)
    assert len(pts) == 1, "unchanged file must not produce a second point"
    assert str(pts[0].id) == first_id
    assert pts[0].payload["mtime"] == first_mtime


def test_changed_file_reembedded_into_same_point(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    img = _make_png(src, "a.png")

    _initial_sync(monkeypatch, raw, src)
    pts = _points(raw)
    assert len(pts) == 1
    old_id = str(pts[0].id)
    old_vec = list(pts[0].vector)

    # Modify file: new size + mtime. Bump mtime explicitly so the
    # change is visible regardless of filesystem timestamp granularity.
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        pytest.skip("Pillow not available")
    Image.new("RGB", (32, 32), (255, 0, 0)).save(img)
    now = time.time()
    os.utime(img, (now + 5, now + 5))

    rc = _run(monkeypatch, raw, src)
    assert rc == 0

    pts = _points(raw)
    assert len(pts) == 1, "changed file must re-embed IN PLACE, not duplicate"
    assert str(pts[0].id) == old_id, "point id must stay stable across re-embed"
    assert list(pts[0].vector) != old_vec, "re-embed must update the vector"


def test_legacy_point_without_mtime_gets_reembedded(monkeypatch, tmp_path):
    """Points indexed before change detection lack mtime/size in the
    payload. They must be treated as changed so they heal on next run."""
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    img = _make_png(src, "a.png")

    _initial_sync(monkeypatch, raw, src)
    pid = str(id_for(img, ""))
    # Strip mtime/size to simulate legacy index state.
    raw.overwrite_payload(
        collection_name=COLLECTION,
        points=[pid],
        payload={"mtime": None, "size": None},
    )

    rc = _run(monkeypatch, raw, src)
    assert rc == 0
    pts = _points(raw)
    assert len(pts) == 1
    assert pts[0].payload.get("mtime") is not None, "legacy point must heal"


def test_dry_run_reports_would_embed_without_writing(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    _make_png(src, "a.png")
    _make_png(src, "b.png")

    rc = _run(monkeypatch, raw, src, extra_args=["--dry-run"])
    assert rc == 0
    assert _points(raw) == [], "dry-run must not write points"


def test_prune_dry_run_still_no_writes(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    _make_png(src, "a.png")
    _initial_sync(monkeypatch, raw, src)
    (src / "a.png").unlink()

    rc = _run(monkeypatch, raw, src, extra_args=["--prune", "--dry-run"])
    assert rc == 0
    pts = _points(raw)
    assert len(pts) == 1, "dry-run prune must not delete points"


def test_full_flag_mutually_exclusive_with_backfill_flags(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    monkeypatch.setattr(local_sync_mod, "make_client", lambda _: raw)
    rc = local_sync_mod.main([
        "--source", str(src), "--source-name", "x",
        "--qdrant-collection", COLLECTION,
        "--full", "--reblurhash",
    ])
    assert rc == 2


def test_full_sweep_embeds_new_and_heals_legacy(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    img = _make_png(src, "a.png")

    _initial_sync(monkeypatch, raw, src)

    # Simulate a legacy point missing blurhash + fingerprint.
    pid = str(id_for(img, ""))
    pts = _points(raw)
    legacy = dict(pts[0].payload)
    legacy["blurhash"] = None
    legacy.pop("content_sha256", None)
    legacy.pop("dhash", None)
    raw.overwrite_payload(
        collection_name=COLLECTION, points=[pid], payload=legacy,
    )

    rc = _run(monkeypatch, raw, src, extra_args=["--full"])
    assert rc == 0

    pts = _points(raw)
    assert len(pts) == 1
    assert pts[0].payload.get("blurhash") is not None, "blurhash healed"
    assert pts[0].payload.get("content_sha256") is not None, "sha healed"
    assert pts[0].payload.get("dhash") is not None, "dhash healed"


def test_full_dry_run_writes_nothing(monkeypatch, tmp_path):
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    _make_png(src, "a.png")
    rc = _run(monkeypatch, raw, src, extra_args=["--full", "--dry-run"])
    assert rc == 0
    assert _points(raw) == [], "dry-run --full must not write"


def test_id_mismatch_diagnostic_zero_on_happy_path(monkeypatch, tmp_path):
    """Happy path: no id mismatch — file id is stable across runs."""
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    _make_png(src, "a.png")
    _initial_sync(monkeypatch, raw, src)
    rc = _run(monkeypatch, raw, src)
    assert rc == 0


def test_id_mismatch_diagnostic_catches_normalisation_drift(
    monkeypatch, tmp_path, capsys,
):
    """Diagnostic: when the same path hashes to two different ids (e.g.
    one computed from `as_posix()`, the other from `resolve()`), the
    indexer should still count it as a mismatch and surface a sample
    so we can see the actual strings involved.

    Simulates the divergence by monkeypatching `Path.resolve` so it
    returns a path whose `as_posix()` differs from the input. This is
    the Windows/SMB normalisation failure mode (different UNC path,
    drive-letter case, symlink resolution) that motivates the
    diagnostic — on POSIX they're normally identical.

    Uses `--dry-run` so the test asserts the change-detection
    behaviour without paying the embed+upsert cost. The diagnostic is
    purely a logging concern; we don't need the vector round-trip to
    prove it fires.
    """
    raw = QdrantClient(location=":memory:")
    src = tmp_path / "img"
    src.mkdir()
    img = _make_png(src, "a.png")

    # Create the collection up-front (1536-dim mock encoder). Lets us
    # pre-seed with a known point before the indexer's first run.
    from indexer.upsert import ensure_collection
    ensure_collection(raw, COLLECTION, dim=1536)

    # Patch Path.resolve so it returns a sibling filename for `img`
    # only. Everything else resolves normally. Activated BEFORE we
    # compute `pid_resolve` below so the patched form is the one we
    # seed into Qdrant.
    original_resolve = Path.resolve

    def patched_resolve(self, *args, **kwargs):
        if self == img:
            return Path(str(self).replace("/a.png", "/A.png"))
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", patched_resolve)

    # Pre-seed with the id of the resolve() variant — the id the
    # indexer will compute second inside the diagnostic branch.
    pid_resolve = id_for(img.resolve(), "")
    pid_as_is = id_for(img, "")
    assert pid_as_is != pid_resolve, (
        "test fixture broken: id_for(p) and id_for(p.resolve()) must "
        "differ for this diagnostic to be exercised"
    )
    raw.upsert(
        collection_name=COLLECTION,
        points=[qmodels.PointStruct(
            id=pid_resolve,
            vector=[0.1] * 1536,
            # `collection="x"` matters: `_scroll_existing_meta`
            # filters the source-scoped scroll on `collection=src_name`.
            # Without it the scroll wouldn't return this point and the
            # fallback (per-batch retrieve) would also miss it (the
            # batch's ids are pid_as_is, not pid_resolve). The point
            # would then be invisible to the change-detection lookup
            # and the diagnostic branch would never fire.
            payload={
                "mtime": int(img.stat().st_mtime),
                "size": int(img.stat().st_size),
                "collection": "x",
            },
        )],
    )

    # Capture stdout so we can assert the diagnostic event surfaced
    # the sample. Local sync emits one JSON line per event when
    # `--json-progress` is set; we add it for this test only.
    #
    # NB: no --dry-run here — `_scroll_existing_meta` is skipped in
    # dry-run mode (see `if not args.rebuild and not args.dry_run`),
    # which would starve the diagnostic's source_meta lookup. We
    # instead stub `upsert.upsert_batch` to a no-op so the embed/upsert
    # path runs (and proves the change-detection decides to re-embed)
    # without paying for the actual vector write. The local qdrant's
    # COSINE normalisation trips over a numpy shape bug when a
    # pre-seeded point with vector=[0.1]*N sits in the collection and
    # the indexer's vector write goes through a different code path —
    # this stub sidesteps that.
    from indexer import upsert as upsert_mod
    monkeypatch.setattr(upsert_mod, "upsert_batch", lambda *a, **kw: None)
    # Stub the post-upsert visibility wait too — the stubbed
    # `upsert_batch` doesn't actually write points, so the
    # last-written-id polling would block until timeout. The
    # diagnostic state lives in the JSON event we capture, so the
    # visibility wait is irrelevant for what this test proves.
    monkeypatch.setattr(
        local_sync_mod, "_await_points_visible", lambda *a, **kw: None,
    )
    monkeypatch.setattr(local_sync_mod, "make_client", lambda _: raw)

    args = [
        "--source", str(src), "--source-name", "x",
        "--qdrant-collection", COLLECTION,
        "--device", "cpu",
        "--model", "mock-1536",
        "--json-progress",
    ]
    rc = local_sync_mod.main(args)
    assert rc == 0

    # Re-parse the captured stdout from pytest's capsys. Using capsys
    # (rather than redirect_stdout) avoids fighting pytest's own
    # capture machinery — the `--json-progress` writes go through
    # sys.stdout and pytest captures them as `capsys.readouterr().out`.
    out = capsys.readouterr().out
    # _emit_progress uses `separators=(",", ":")` so the line is
    # `{"event":"done",...}` — no space after the colon. Match both
    # with-and-without-space variants in case the local Python ever
    # switches to default separators.
    done_events = [
        line for line in out.splitlines()
        if line.startswith("{") and '"event": "done"' in line
        or '"event":"done"' in line
    ]
    assert done_events, (
        f"expected a 'done' JSON event in stdout; got:\n{out!r}"
    )
    import json as _json
    done = _json.loads(done_events[-1])
    assert done["id_mismatch"] >= 1, (
        f"diagnostic should have fired; got id_mismatch={done['id_mismatch']}, "
        f"sample={done['id_mismatch_sample']}"
    )
    assert done["id_mismatch_sample"], "sample must be non-empty"
    sample = done["id_mismatch_sample"][0]
    # The sample records the raw strings that produced each id.
    # Whatever the exact divergence is, both `id` and `id_alt` must
    # be populated and must be different UUIDs.
    assert sample["id"] != sample["id_alt"]
    assert sample["path"] == str(img)
    # `path_resolved` is computed via the patched resolve, so it
    # should not equal the input `path` for this image.
    assert sample["path_resolved"] != str(img)
    # Recorded (mtime, size) came from the pre-seeded point; current
    # values came from the on-disk image at scan time.
    assert sample["recorded_mtime"] == int(img.stat().st_mtime)
    assert sample["recorded_size"] == int(img.stat().st_size)


def test_split_read_write_collections_skip_already_indexed(
    monkeypatch, tmp_path,
):
    """Regression guard: when the indexer writes to a staging
    collection (`--qdrant-collection X`) but reads from a canonical
    collection (`--qdrant-read-collection Y`), it must compare
    against Y, not X. Without this, every walk classifies every
    file as new because X only ever holds ~30 in-flight points.

    This is the prod scenario: write=`images_pending`, read=`images`.
    """
    # Two in-memory Qdrant instances so we can simulate two separate
    # collections on a single connection-less backend. Each
    # `QdrantClient(location=":memory:")` is its own isolated store.
    write_client = QdrantClient(location=":memory:")
    read_client = QdrantClient(location=":memory:")
    write_client.create_collection(
        collection_name="staging",
        vectors_config=qmodels.VectorParams(size=1536, distance=qmodels.Distance.COSINE),
    )
    read_client.create_collection(
        collection_name="canonical",
        vectors_config=qmodels.VectorParams(size=1536, distance=qmodels.Distance.COSINE),
    )
    # Payload index on `collection` — same as the prod search side.
    read_client.create_payload_index(
        collection_name="canonical",
        field_name="collection",
        field_type="keyword",
    )

    # Pre-seed the canonical collection with one already-indexed
    # file. Its mtime/size must match the file on disk for the
    # skip to fire.
    src = tmp_path / "img"
    src.mkdir()
    img = _make_png(src, "a.png")
    pid = id_for(img, "")
    st = img.stat()
    read_client.upsert(
        collection_name="canonical",
        points=[qmodels.PointStruct(
            id=pid,
            vector=[0.1] * 1536,
            payload={
                "mtime": int(st.st_mtime),
                "size": int(st.st_size),
                "collection": "x",
            },
        )],
    )

    # Stub the two Qdrant clients. The indexer calls
    # `QdrantClient(...)` once with the URL, and we return the write
    # client. The read client is wired via a side-channel: the
    # indexer's `client` attribute is the write client, but the
    # `--qdrant-read-collection` flag steers it to the read client
    # through Qdrant's HTTP API. That doesn't translate to in-memory
    # clients, so we monkeypatch `client.get_collection` and
    # `client.scroll` / `client.retrieve` to consult the read client
    # when the collection name is `canonical`.
    from indexer import local_sync as local_sync_mod
    monkeypatch.setattr(local_sync_mod, "make_client", lambda _: write_client)

    real_get_collection = write_client.get_collection
    real_scroll = write_client.scroll
    real_retrieve = write_client.retrieve

    def routed_get_collection(name, **kw):
        if name == "canonical":
            return read_client.get_collection(name, **kw)
        return real_get_collection(name, **kw)

    def routed_scroll(*, collection_name, **kw):
        if collection_name == "canonical":
            return read_client.scroll(collection_name=collection_name, **kw)
        return real_scroll(collection_name=collection_name, **kw)

    def routed_retrieve(collection_name, **kw):
        if collection_name == "canonical":
            return read_client.retrieve(collection_name, **kw)
        return real_retrieve(collection_name, **kw)

    monkeypatch.setattr(write_client, "get_collection", routed_get_collection)
    monkeypatch.setattr(write_client, "scroll", routed_scroll)
    monkeypatch.setattr(write_client, "retrieve", routed_retrieve)

    rc = local_sync_mod.main([
        "--source", str(src), "--source-name", "x",
        "--qdrant-collection", "staging",
        "--qdrant-read-collection", "canonical",
        "--device", "cpu",
        "--model", "mock-1536",
    ])
    assert rc == 0

    # If change-detection correctly consulted `canonical`, the
    # already-indexed file should be skipped (no write to staging).
    staging_pts, _ = write_client.scroll(
        collection_name="staging", limit=10, with_payload=False,
    )
    assert len(staging_pts) == 0, (
        "split-collection setup broken: indexer wrote to staging "
        "despite the file being already indexed in canonical — "
        "change-detection is reading from the wrong collection"
    )

    # And the canonical collection is untouched (no upsert from this
    # run; it would mean the indexer wrote through the wrong
    # collection).
    canonical_pts, _ = read_client.scroll(
        collection_name="canonical", limit=10, with_payload=False,
    )
    assert len(canonical_pts) == 1, (
        "split-collection setup broken: indexer wrote to canonical "
        "(read target) when it should only have read from it"
    )
