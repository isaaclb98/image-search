"""
tests/test_sample_centroid.py — unit tests for the K-of-N sample
centroid helper. Pinned in tests/ (not search/tests/) so they live
next to the existing centroid math coverage and can import without
going through the FastAPI app.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from search.centroids_compute import (
    DEFAULT_SAMPLE_K,
    sample_centroid,
)


def _vec(x: float) -> list[float]:
    """Build a 1D unit-ish vector along the x axis of an N-D space."""
    out = [0.0] * 8
    out[int(x) % 8] = 1.0
    return out


def test_default_k_is_ten():
    # Pin the spec'd default so a future bump to 15 (or whatever)
    # shows up as a deliberate change in this test.
    assert DEFAULT_SAMPLE_K == 10


def test_picks_k_when_n_is_larger():
    seed_ids = [f"id-{i}" for i in range(50)]
    vectors = [_vec(float(i)) for i in range(50)]
    _centroid, picked, picked_ids = sample_centroid(
        seed_ids, vectors, k=10, seed=42,
    )
    assert picked == 10
    assert len(picked_ids) == 10
    # No duplicates — sample() without replacement.
    assert len(set(picked_ids)) == 10
    # All picked ids are from the input.
    assert set(picked_ids).issubset(set(seed_ids))


def test_deterministic_with_seed():
    # Same seed + same input → same picked set. Used by route
    # if we ever want to round-trip the seed through the URL.
    seed_ids = [f"id-{i}" for i in range(20)]
    vectors = [_vec(float(i)) for i in range(20)]
    _c1, n1, ids1 = sample_centroid(seed_ids, vectors, k=10, seed=7)
    _c2, n2, ids2 = sample_centroid(seed_ids, vectors, k=10, seed=7)
    assert ids1 == ids2
    assert n1 == n2

    # Different seed → (almost certainly) different picked set.
    _c3, _n3, ids3 = sample_centroid(seed_ids, vectors, k=10, seed=8)
    assert ids3 != ids1


def test_falls_back_to_full_set_when_n_le_k():
    # For very small albums (≤ k likes) we use the whole input
    # rather than over-sample. The whole-input mean is the
    # same number the existing centroid path returns, so the
    # sample mode is effectively a no-op for tiny populations
    # — keeps the comparison honest.
    seed_ids = ["a", "b", "c", "d", "e"]
    vectors = [_vec(0), _vec(1), _vec(2), _vec(3), _vec(4)]
    centroid, picked, picked_ids = sample_centroid(
        seed_ids, vectors, k=10, seed=42,
    )
    assert picked == 5
    assert set(picked_ids) == set(seed_ids)
    # Result must be unit length.
    norm = math.sqrt(sum(v * v for v in centroid))
    assert math.isclose(norm, 1.0, abs_tol=1e-5)


def test_centroid_is_unit_length():
    # Output vector must be L2-normalised — the search route
    # feeds it straight into Qdrant cosine search, which assumes
    # unit-length inputs (or at least makes no guarantees about
    # the score scale otherwise).
    seed_ids = [f"id-{i}" for i in range(100)]
    vectors = [_vec(float(i % 8)) for i in range(100)]
    centroid, _n, _ids = sample_centroid(seed_ids, vectors, k=10, seed=99)
    norm = math.sqrt(sum(v * v for v in centroid))
    assert math.isclose(norm, 1.0, abs_tol=1e-5)


def test_centroid_matches_manual_mean_of_subset():
    # Belt-and-braces: when seeded, the picked ids should match
    # the same indices we'd pick with Python's random.Random,
    # and the centroid should equal the unit-normalised mean of
    # those exact rows.
    seed_ids = [f"id-{i}" for i in range(30)]
    vectors = [_vec(float(i % 8)) for i in range(30)]
    import random
    expected_indices = random.Random(123).sample(range(30), 10)
    expected_ids = [seed_ids[i] for i in expected_indices]
    expected_mean = np.mean(
        [vectors[i] for i in expected_indices], axis=0,
    )
    expected_mean = expected_mean / np.linalg.norm(expected_mean)

    centroid, n, ids = sample_centroid(seed_ids, vectors, k=10, seed=123)
    assert ids == expected_ids
    assert n == 10
    np.testing.assert_allclose(centroid, expected_mean, atol=1e-6)


def test_rejects_empty_input():
    with pytest.raises(ValueError, match="at least one vector"):
        sample_centroid([], [], k=10)


def test_rejects_zero_k():
    seed_ids = ["a"]
    vectors = [_vec(0)]
    with pytest.raises(ValueError, match="k must be > 0"):
        sample_centroid(seed_ids, vectors, k=0)


def test_rejects_length_mismatch():
    with pytest.raises(ValueError, match="length mismatch"):
        sample_centroid(["a", "b"], [_vec(0)], k=10)