"""
tests/test_l2_median_centroid.py — unit tests for the
geometric-median helper (round-77).

Mirrors the structure of test_sample_centroid.py:
sanity tests on synthetic data + edge cases.
"""
import time
import numpy as np
import pytest
from search.centroids_compute import l2_median_centroid


class TestSanity:
    """Math sanity: median is well-defined and matches
    closed-form answers on simple inputs."""

    def test_collinear_three_points(self):
        """Three collinear points: median is the middle point."""
        ids = ["a", "b", "c"]
        vecs = [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0]]
        centroid, picked, picked_ids = l2_median_centroid(ids, vecs)
        # Weiszfeld returns a point that minimizes sum of squared
        # distances. For collinear 3 points at (1,0), (2,0), (3,0),
        # the median is exactly (2,0). After unit-normalization,
        # that's [1, 0].
        assert np.allclose(centroid, [1.0, 0.0], atol=0.01)
        assert picked == 3
        assert picked_ids == ids

    def test_symmetric_triangle_fermat(self):
        """Equilateral triangle: Fermat point is the centroid,
        and the Fermat point unit-normalized is [√3/2, 1/2]."""
        ids = ["a", "b", "c"]
        vecs = [[0.0, 0.0], [2.0, 0.0], [1.0, np.sqrt(3)]]
        centroid, _, _ = l2_median_centroid(ids, vecs)
        # Geometric median of equilateral triangle vertices
        # = centroid = (1, √3/3). After L2-normalization,
        # that's [√3/2, 1/2] (cos 60° = 0.5, sin 60° = √3/2).
        expected = [np.sqrt(3) / 2, 0.5]
        assert np.allclose(centroid, expected, atol=0.01)

    def test_outlier_robustness(self):
        """90% near origin + 10% at (-10,-10,-10,-10):
        median is closer to origin than mean (relative robustness)."""
        rng = np.random.default_rng(42)
        cluster = rng.normal(0, 0.3, (90, 4))
        outliers = np.full((10, 4), -10.0)
        pts = np.concatenate([cluster, outliers])
        ids = [f"p{i}" for i in range(100)]
        centroid, _, _ = l2_median_centroid(ids, pts.tolist())
        median = np.asarray(centroid)
        # Mean (un-normalized) is at roughly -1 per dim
        # (90 * 0 + 10 * -10) / 100 = -1.
        # Median is pulled toward outliers too (10% of weight is
        # substantial) but less — at ~-0.5 per dim after unit-norm.
        # Median direction should be CLOSER to origin than mean.
        median_neg = -median.mean()  # negative → toward outlier side
        mean_neg = -pts.mean(axis=0).mean()  # ~-1.0
        assert median_neg < mean_neg, (
            f"median should be closer to origin than mean: "
            f"median={median_neg:.3f}, mean={mean_neg:.3f}"
        )


class TestContract:
    """Documented return shape."""

    def test_centroid_is_unit_vector(self):
        """Returned centroid is unit-length (cosine-search ready)."""
        ids = ["a", "b", "c"]
        vecs = [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0]]
        centroid, _, _ = l2_median_centroid(ids, vecs)
        assert np.isclose(np.linalg.norm(centroid), 1.0)

    def test_picked_count_equals_n(self):
        """All inputs contribute — no random subset."""
        ids = ["a", "b", "c", "d", "e"]
        vecs = [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0], [4.0, 0.0], [5.0, 0.0]]
        _, picked, picked_ids = l2_median_centroid(ids, vecs)
        assert picked == 5
        assert picked_ids == ids

    def test_deterministic(self):
        """Same input → same output (no randomness)."""
        ids = ["a", "b", "c", "d"]
        vecs = [[1.0, 0.5, 0.0], [2.0, 0.0, 1.0], [3.0, 1.0, 0.5], [0.5, 0.5, 0.5]]
        c1, _, _ = l2_median_centroid(ids, vecs)
        c2, _, _ = l2_median_centroid(ids, vecs)
        assert np.allclose(c1, c2)

    def test_high_dimensional_realistic(self):
        """200 768-dim vectors (realistic SigLIP2 shape)."""
        rng = np.random.default_rng(42)
        pts = rng.normal(0, 0.3, (200, 768))
        ids = [f"p{i}" for i in range(200)]
        centroid, picked, picked_ids = l2_median_centroid(ids, pts.tolist())
        assert len(centroid) == 768
        assert np.isclose(np.linalg.norm(centroid), 1.0, atol=1e-5)
        assert picked == 200
        assert len(picked_ids) == 200

    def test_speed_500x768(self):
        """500×768 in <50ms (typical album + small album combined)."""
        rng = np.random.default_rng(42)
        pts = rng.normal(0, 0.3, (500, 768))
        ids = [f"p{i}" for i in range(500)]
        start = time.time()
        l2_median_centroid(ids, pts.tolist())
        elapsed_ms = (time.time() - start) * 1000
        assert elapsed_ms < 50, f"too slow: {elapsed_ms:.1f}ms"


class TestEdgeCases:
    """Inputs that should not crash."""

    def test_single_vector(self):
        """One vector: median = that vector (normalized)."""
        ids = ["a"]
        vecs = [[3.0, 4.0]]  # norm 5
        centroid, picked, _ = l2_median_centroid(ids, vecs)
        # Should normalize to unit length
        assert np.isclose(np.linalg.norm(centroid), 1.0)
        # Direction preserved
        assert np.allclose(centroid, [0.6, 0.8], atol=1e-5)
        assert picked == 1

    def test_identical_vectors(self):
        """All vectors identical: median = that vector."""
        ids = ["a", "b", "c"]
        vecs = [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]
        centroid, picked, _ = l2_median_centroid(ids, vecs)
        assert np.allclose(centroid, [1.0, 0.0], atol=1e-4)
        assert picked == 3

    def test_two_vectors(self):
        """Two vectors: median is between them on the line."""
        ids = ["a", "b"]
        vecs = [[1.0, 0.0], [3.0, 0.0]]
        centroid, picked, _ = l2_median_centroid(ids, vecs)
        # Median of two points is any point between them. Should
        # land on the line x-axis with x in [1, 3].
        assert abs(centroid[1]) < 0.01
        assert 1.0 <= centroid[0] <= 3.0


class TestErrors:
    """Inputs that should raise."""

    def test_empty_input(self):
        with pytest.raises(ValueError, match="at least one vector"):
            l2_median_centroid([], [])


class TestComparisonToMean:
    """Documented behaviour: median is closer to dominant mode
    than mean when outliers are present."""

    def test_median_inside_cluster_mean_outside(self):
        """When the dominant cluster is at +1 and outliers are
        at -10, the median stays in the +1 cluster while the
        mean drifts toward -10 (relative robustness)."""
        rng = np.random.default_rng(42)
        cluster = rng.normal(1.0, 0.2, (100, 4))  # tight at +1
        outliers = np.full((20, 4), -10.0)         # 20% at -10
        pts = np.concatenate([cluster, outliers])
        ids = [f"p{i}" for i in range(120)]
        median, _, _ = l2_median_centroid(ids, pts.tolist())
        # Mean (un-normalized) drifts toward -10:
        # 100 * 1 + 20 * (-10) / 120 ≈ -0.83 per dim.
        mean = pts.mean(axis=0)
        # Median stays in the +1 cluster because the 20 outliers
        # contribute only 1/distance weight each. Asserts the
        # median is closer to the +1 cluster center than the
        # mean is — i.e. median is more robust to outliers.
        median_distance_to_cluster = np.linalg.norm(median - np.array([1.0, 0, 0, 0]))
        mean_distance_to_cluster = np.linalg.norm(mean - np.array([1.0, 0, 0, 0]))
        assert median_distance_to_cluster < mean_distance_to_cluster, (
            f"median should be closer to dominant cluster than mean: "
            f"median dist={median_distance_to_cluster:.3f}, mean dist={mean_distance_to_cluster:.3f}"
        )