"""
benchmarks/test_indexing_throughput.py

Measures images-per-second through the indexer using:
  - in-memory Qdrant (no network)
  - mock 1536-dim encoder (no model, no GPU)
  - synthetic JPEG corpus (no real photos)

Goal: establish a stable baseline against which Tier-2.1 (concurrent PIL
decode) can be compared. Numbers are reported, not asserted — this is a
measurement harness, not a regression test.

Run:
    .venv-test/bin/python -m pytest benchmarks/test_indexing_throughput.py -v -s
"""

from __future__ import annotations

import time

import pytest

# Synthetic corpus sizes to sweep. Small enough to run on any host in
# seconds; large enough to be representative. Increase upper bound when
# the harness stabilizes.
SIZES = [100, 500]


def _percentile(sorted_ms: list[float], pct: float) -> float:
    """Linear-interpolated percentile in milliseconds."""
    if not sorted_ms:
        return 0.0
    k = (len(sorted_ms) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_ms) - 1)
    if f == c:
        return sorted_ms[f]
    return sorted_ms[f] + (sorted_ms[c] - sorted_ms[f]) * (k - f)


@pytest.mark.parametrize("synth_corpus", SIZES, indirect=True)
def test_indexing_throughput_baseline(synth_corpus, qdrant_in_memory, capsys):
    """Measure end-to-end indexer throughput on N synthetic images.

    Pipeline exercised:
      scan -> image_loader (PIL decode + letterbox + normalize) ->
      vision_encoder (mock embed) -> upsert (Qdrant write)
    """
    from indexer import scan as scan_mod
    from indexer.image_loader import load
    from indexer.upsert import DEFAULT_COLLECTION, build_payload, id_for
    from indexer.vision_encoder import _mock_image_embed

    paths = scan_mod.list_image_paths(synth_corpus)

    # One scan timing.
    t_scan = time.perf_counter()
    paths_scanned = scan_mod.list_image_paths(synth_corpus)
    scan_ms = (time.perf_counter() - t_scan) * 1000.0

    # Per-image: load (PIL decode + transform) + mock embed (instant) + upsert.
    # Batched upsert at end to amortize Qdrant round-trips.
    batch_size = 16
    load_ms_list: list[float] = []
    embed_ms_list: list[float] = []
    pending: list[tuple[str, list[float], dict]] = []

    t0 = time.perf_counter()
    for path in paths:
        t_l0 = time.perf_counter()
        tensor = load(path)
        load_ms_list.append((time.perf_counter() - t_l0) * 1000.0)

        t_e0 = time.perf_counter()
        vec = _mock_image_embed(hash(path.as_posix()))
        embed_ms_list.append((time.perf_counter() - t_e0) * 1000.0)

        point_id = id_for(path)
        payload = build_payload(
            path=path,
            shard="",
            collection=DEFAULT_COLLECTION,
            vector=vec,
            tensor=tensor,
        )
        pending.append((point_id, vec, payload))

        if len(pending) >= batch_size:
            _upsert_batch(qdrant_in_memory, pending)
            pending.clear()

    if pending:
        _upsert_batch(qdrant_in_memory, pending)

    total_ms = (time.perf_counter() - t0) * 1000.0
    n = len(paths)

    load_p50 = _percentile(sorted(load_ms_list), 0.50)
    load_p95 = _percentile(sorted(load_ms_list), 0.95)
    embed_p50 = _percentile(sorted(embed_ms_list), 0.50)

    # Verify the upserts landed.
    from indexer.upsert import id_for as _id_for

    point_count = qdrant_in_memory.client.count(
        collection_name=qdrant_in_memory.collection, exact=True
    ).count
    assert point_count == n, f"expected {n} upserts, got {point_count}"

    throughput_img_per_s = (n / total_ms) * 1000.0 if total_ms > 0 else 0.0

    with capsys.disabled():
        print()
        print(f"=== INDEXING THROUGHPUT (n={n}) ===")
        print(f"  scan          : {scan_ms:7.2f} ms ({len(paths_scanned)} paths)")
        print(f"  total         : {total_ms:7.2f} ms")
        print(f"  throughput    : {throughput_img_per_s:7.2f} img/s")
        print(f"  PIL load p50  : {load_p50:7.2f} ms")
        print(f"  PIL load p95  : {load_p95:7.2f} ms")
        print(f"  mock embed p50: {embed_p50:7.3f} ms (no real model)")
        print(f"  Qdrant points : {point_count}")

    # Sanity guard: must have indexed every image.
    assert point_count == n


def _upsert_batch(qdrant_search, pending: list[tuple[str, list[float], dict]]) -> None:
    """One Qdrant upsert call for the whole batch."""
    from qdrant_client.http import models as qmodels

    points = [
        qmodels.PointStruct(id=pid, vector=vec, payload=payload)
        for pid, vec, payload in pending
    ]
    qdrant_search.client.upsert(
        collection_name=qdrant_search.collection,
        points=points,
        wait=True,
    )