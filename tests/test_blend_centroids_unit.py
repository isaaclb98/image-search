"""Unit tests for search.centroids.blend_centroids / composite_centroid_name.

Pure-function tests — no Qdrant, no app factory. The blend helper
is the math layer of the multi-centroid feature and the place
where any silent-failure mode (e.g. averaging across embedding
spaces) would land. Pinning the contract here means the API tests
can stay focused on URL plumbing.
"""
from __future__ import annotations

import math

import pytest

from search.centroids import blend_centroids, composite_centroid_name


def test_single_entry_default_weights_returns_renormalised_input() -> None:
    """With one centroid and no weights, the output equals the input."""
    v = [3.0, 4.0]
    v = [v[0] / 5.0, v[1] / 5.0]
    out = blend_centroids([(v, "only")], None)
    assert out == pytest.approx([v[0], v[1]], abs=1e-9)
    # Unit length invariant.
    norm = math.sqrt(sum(x * x for x in out))
    assert norm == pytest.approx(1.0, abs=1e-9)


def test_equal_weight_antipodal_collapses_to_zero() -> None:
    """Two antipodal unit vectors, equal weight -> zero, but re-renorm
    raises ValueError. This is the failure mode we want to surface
    loudly rather than ship a zero-vector query to Qdrant."""
    v_a = [1.0, 0.0]
    v_b = [-1.0, 0.0]
    with pytest.raises(ValueError, match="collapsed to zero"):
        blend_centroids([(v_a, "a"), (v_b, "b")], None)


def test_orthogonal_equal_weights() -> None:
    """Two orthogonal unit vectors blend to a 45-degree unit vector.

    (1,0) + (0,1) = (1,1); norm sqrt(2); normalised -> (1/sqrt(2), 1/sqrt(2)).
    """
    out = blend_centroids([([1.0, 0.0], "x"), ([0.0, 1.0], "y")], None)
    assert out == pytest.approx([math.sqrt(0.5), math.sqrt(0.5)], abs=1e-9)


def test_weights_skew_blend() -> None:
    """A weight-3 on X vs weight-1 on Y steers the blend toward X."""
    out = blend_centroids(
        [([1.0, 0.0], "x"), ([0.0, 1.0], "y")],
        [3.0, 1.0],
    )
    # (3, 1) / sqrt(10) approx (0.9487, 0.3162).
    expected_norm = math.sqrt(3.0 * 3.0 + 1.0 * 1.0)
    assert out == pytest.approx(
        [3.0 / expected_norm, 1.0 / expected_norm], abs=1e-9,
    )


def test_short_weights_list_is_padded_with_ones() -> None:
    """Fewer weights than entries pads with 1.0 (defensive against
    URL parsing giving shorter-than-expected lists)."""
    # [2.0] on 3 entries pads to [2.0, 1.0, 1.0].
    # (2,0)+(0,1)+(1,1) = (3,2); norm sqrt(13).
    out = blend_centroids(
        [([1.0, 0.0], "x"), ([0.0, 1.0], "y"), ([1.0, 1.0], "z")],
        [2.0],
    )
    expected = [3.0 / math.sqrt(13.0), 2.0 / math.sqrt(13.0)]
    assert out == pytest.approx(expected, abs=1e-9)


def test_long_weights_list_truncated() -> None:
    """More weights than entries: extras are ignored (defensive)."""
    out = blend_centroids(
        [([1.0, 0.0], "x"), ([0.0, 1.0], "y")],
        [2.0, 1.0, 999.0, -1.0],  # extras ignored
    )
    expected_norm = math.sqrt(4.0 + 1.0)
    assert out == pytest.approx(
        [2.0 / expected_norm, 1.0 / expected_norm], abs=1e-9,
    )


def test_zero_total_weight_rejected() -> None:
    """Weights summing to zero or below collapse the blend to a
    zero vector on renormalisation. The helper rejects this loudly
    rather than ship a zero query vector to Qdrant.

    Per-weight positivity is enforced at the API layer
    (`_parse_weights` returns 400). The helper's contract is the
    total-weight check -- catches cases like `[-1, 1]` where the
    total is zero even though no single weight is non-positive.
    """
    with pytest.raises(ValueError, match="must be positive"):
        blend_centroids(
            [([1.0, 0.0], "x"), ([0.0, 1.0], "y")],
            [-1.0, 1.0],
        )
    with pytest.raises(ValueError, match="must be positive"):
        blend_centroids(
            [([1.0, 0.0], "x"), ([0.0, 1.0], "y")],
            [1.0, -1.0],
        )
    with pytest.raises(ValueError, match="must be positive"):
        blend_centroids(
            [([1.0, 0.0], "x"), ([0.0, 1.0], "y")],
            [0.0, 0.0],
        )


def test_empty_input_rejected() -> None:
    """Need at least one centroid to blend."""
    with pytest.raises(ValueError, match="at least one centroid"):
        blend_centroids([], None)


def test_mismatched_dim_rejected() -> None:
    """Two vectors of different length -> ValueError. The server
    is expected to have validated same-dim upstream, but the helper
    is the last line of defence and must not silently zero-pad."""
    with pytest.raises(ValueError, match="dim 2 != subsequent vector dim 3"):
        blend_centroids(
            [([1.0, 0.0], "a"), ([0.0, 1.0, 0.0], "b")],
            None,
        )


def test_expected_dim_mismatch_rejected() -> None:
    """Caller-supplied expected_dim acts as a guard against accidental
    cross-model blends. A 4-dim vector against expected_dim=1536
    is rejected loudly."""
    with pytest.raises(ValueError, match="dim 4 != expected 1536"):
        blend_centroids(
            [([1.0, 0.0, 0.0, 0.0], "small")],
            None,
            expected_dim=1536,
        )


def test_expected_dim_none_skips_check() -> None:
    """expected_dim=None means the caller opted out -- useful when
    blending only dynamic centroids (which carry no metadata)."""
    out = blend_centroids(
        [([1.0, 0.0], "a")],
        None,
        expected_dim=None,
    )
    assert out == pytest.approx([1.0, 0.0], abs=1e-9)


def test_three_centroid_blend_unit_norm_invariant() -> None:
    """The unit-norm invariant holds for any non-degenerate blend."""
    out = blend_centroids(
        [
            ([1.0, 0.0, 0.0], "x"),
            ([0.0, 1.0, 0.0], "y"),
            ([0.0, 0.0, 1.0], "z"),
        ],
        [1.0, 2.0, 3.0],
    )
    norm = math.sqrt(sum(x * x for x in out))
    assert norm == pytest.approx(1.0, abs=1e-9)


# ---------------- composite_centroid_name ----------------


def test_composite_name_two_centroids() -> None:
    assert composite_centroid_name(["wuxia", "portrait"]) == "wuxia+portrait"


def test_composite_name_three_centroids() -> None:
    assert composite_centroid_name(["a", "b", "c"]) == "a+b+c"


def test_composite_name_empty() -> None:
    assert composite_centroid_name([]) == ""


def test_composite_name_single() -> None:
    """Single-element list -- the result is just the name without the `+`."""
    assert composite_centroid_name(["only"]) == "only"
