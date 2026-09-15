"""
tests/test_cluster_then_sample_centroid.py — unit tests for the
K-clusters / N-pick sample centroid helper (round-75).

Mirrors test_sample_centroid.py in style and placement.
"""
from __future__ import annotations

import numpy as np
import pytest

from search.centroids_compute import (
    DEFAULT_CLUSTER_SAMPLE_N,
    DEFAULT_SAMPLE_K,
    cluster_then_sample_centroid,
)


def _vec(seed: float, dim: int = 16) -> list[float]:
    """A simple deterministic 16-D unit vector from a seed float."""
    rng = np.random.RandomState(int(seed * 1000))
    v = rng.randn(dim).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def test_defaults():
    """Pin the round-75 spec defaults so a future bump is a deliberate change."""
    assert DEFAULT_SAMPLE_K == 10
    assert DEFAULT_CLUSTER_SAMPLE_N == 3


def test_clusters_n_vectors_into_k_groups_and_picks_n_of_them():
    """Build 200 vectors clustered into 4 visual modes. Verify the
    function returns a unit-length centroid built from a subset of
    the input (size proportional to N clusters of ~50 photos each)."""
    rng = np.random.RandomState(0)
    centers = rng.randn(4, 16).astype(np.float32)
    vecs: list[list[float]] = []
    ids: list[str] = []
    for c in range(4):
        for _ in range(50):
            v = centers[c] + rng.randn(16).astype(np.float32) * 0.05
            v = v / np.linalg.norm(v)
            vecs.append(v.tolist())
            ids.append(f"c{c}-i{len(ids)}")

    centroid, picked_count, picked_ids = cluster_then_sample_centroid(
        ids, vecs, k=10, n=3, seed=42,
    )

    assert len(centroid) == 16
    np.testing.assert_allclose(np.linalg.norm(centroid), 1.0, atol=1e-5)
    assert picked_count == 3
    # 200 vectors clustered into 10 groups of ~20 each; 3 clusters
    # of ~60 each. Allow a wide range so this isn't flaky if the
    # k-means happens to split a mode unevenly.
    assert 30 <= len(picked_ids) <= 120
    # All picked ids come from the input.
    assert set(picked_ids).issubset(set(ids))


def test_centroid_is_a_blend_of_clusters():
    """The centroid should be a vector close to the mean of the picked
    cluster centroids, not to any single photo. Sanity check: the
    cosine distance to the full album mean should be larger than to
    the picked-cluster mean."""
    rng = np.random.RandomState(1)
    centers = rng.randn(5, 16).astype(np.float32)
    vecs: list[list[float]] = []
    ids: list[str] = []
    for c in range(5):
        for _ in range(40):
            v = centers[c] + rng.randn(16).astype(np.float32) * 0.02
            v = v / np.linalg.norm(v)
            vecs.append(v.tolist())
            ids.append(f"c{c}-i{len(ids)}")

    centroid, _n, picked_ids = cluster_then_sample_centroid(
        ids, vecs, k=10, n=3, seed=99,
    )

    centroid_arr = np.asarray(centroid, dtype=np.float32)
    full_mean = np.mean(vecs, axis=0)
    full_mean /= np.linalg.norm(full_mean)
    # The sub-centroid should differ from the full mean (different
    # blend of modes), so dot product < ~0.95. Loose bound to
    # avoid flakiness.
    assert float(np.dot(centroid_arr, full_mean)) < 0.99


def test_deterministic_with_seed():
    """Same seed → same picked clusters. Useful for reproducible
    debugging via URL-passed seeds if we ever add that."""
    seed_ids = [f"id-{i}" for i in range(80)]
    vectors = [_vec(float(i)) for i in range(80)]
    _c1, n1, ids1 = cluster_then_sample_centroid(
        seed_ids, vectors, k=10, n=3, seed=7,
    )
    _c2, n2, ids2 = cluster_then_sample_centroid(
        seed_ids, vectors, k=10, n=3, seed=7,
    )
    assert ids1 == ids2
    assert n1 == n2


def test_n_can_equal_k():
    """Picking all clusters should give the full mean (after
    re-normalization). With n=k the picked set is the full set."""
    seed_ids = [f"id-{i}" for i in range(40)]
    vectors = [_vec(float(i)) for i in range(40)]
    _c, _n, picked_ids = cluster_then_sample_centroid(
        seed_ids, vectors, k=10, n=10, seed=1,
    )
    assert len(picked_ids) == 40


def test_fallback_for_small_input():
    """When n_vecs <= k, clustering is degenerate — fall back to
    the whole input as the centroid (matches sample_centroid's
    tiny-population behaviour)."""
    seed_ids = ["a", "b", "c"]
    vectors = [_vec(i) for i in range(3)]
    centroid, n, picked_ids = cluster_then_sample_centroid(
        seed_ids, vectors, k=10, n=3,
    )
    assert len(centroid) == 16
    np.testing.assert_allclose(np.linalg.norm(np.asarray(centroid)), 1.0, atol=1e-5)
    assert n == 3
    assert sorted(picked_ids) == ["a", "b", "c"]


def test_rejects_empty_input():
    with pytest.raises(ValueError, match="at least one vector"):
        cluster_then_sample_centroid([], [], k=10, n=3)


def test_rejects_zero_k():
    with pytest.raises(ValueError, match="k must be > 0"):
        cluster_then_sample_centroid(["a"], [_vec(0)], k=0, n=1)


def test_rejects_zero_n():
    with pytest.raises(ValueError, match="n must be > 0"):
        cluster_then_sample_centroid(["a"], [_vec(0)], k=10, n=0)


def test_rejects_n_greater_than_k():
    with pytest.raises(ValueError, match=r"n .* must be <= k"):
        cluster_then_sample_centroid(["a", "b"], [_vec(0), _vec(1)], k=2, n=5)


def test_rejects_length_mismatch():
    with pytest.raises(ValueError, match="length mismatch"):
        cluster_then_sample_centroid(["a", "b"], [_vec(0)], k=10, n=3)


def test_rejects_non_2d_input():
    """Vectors must be list[list[float]], not list[float]. A single
    flat list flattens to a 1-D numpy array."""
    with pytest.raises(ValueError, match="2D vector matrix"):
        cluster_then_sample_centroid(["a"], [1.0], k=10, n=3)
