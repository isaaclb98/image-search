"""Round‑29: tests for `calibrate_near_dup_threshold` and
`filter_near_duplicates` regression coverage.

The original implementation picked the 1st percentile of the
seed set's pairwise distances with `method='lower'`. For small
seed sets containing a tight pair (e.g. two photos of the same
person), that produced a cutoff of ~0.12 — "moderately similar"
rather than "near‑duplicate" — and dropped almost every
candidate, leaving the user with an empty search page.

Fix: use the median (typical seed‑seed distance) and clamp the
upper bound at 0.20. These tests pin both behaviours so the
regression can't sneak back.
"""

from __future__ import annotations

import numpy as np

from search.centroids_compute import (
    calibrate_near_dup_threshold,
    filter_near_duplicates,
    _MAX_NEAR_DUP_THRESHOLD,
)


def _unit(v):
    return (np.asarray(v, dtype=np.float32)
            / np.linalg.norm(v))


def _unit_batch(rows):
    arr = np.asarray(rows, dtype=np.float32)
    return arr / np.linalg.norm(arr, axis=1, keepdims=True)


def test_calibrate_returns_zero_for_empty_only():
    """No seeds → no threshold. With at least one seed, the
    constant 0.02 threshold applies."""
    assert calibrate_near_dup_threshold(None) == 0.0
    assert calibrate_near_dup_threshold([]) == 0.0


def test_calibrate_single_seed_returns_constant_threshold():
    """A single seed photo is enough — the threshold is constant,
    not calibrated against intra-seed statistics."""
    threshold = calibrate_near_dup_threshold([_unit([1, 0]).tolist()])
    assert threshold == _MAX_NEAR_DUP_THRESHOLD


def test_calibrate_constant_for_all_seed_sizes():
    """The fix: the threshold must NOT vary with seed-set size.
    Before, a 2‑photo album produced ~0.12 (1st percentile of
    one tight pair) and a 16‑photo favourites set produced ~0.14
    (1st percentile of 120 distances). Now both return the
    same constant."""
    np.random.seed(0)
    one_seed = [_unit(np.random.randn(64)).tolist()]
    two_seeds = _unit_batch(np.random.randn(2, 64)).tolist()
    nine_seeds = _unit_batch(np.random.randn(9, 64)).tolist()
    sixteen_seeds = _unit_batch(np.random.randn(16, 64)).tolist()

    assert calibrate_near_dup_threshold(one_seed) == _MAX_NEAR_DUP_THRESHOLD
    assert calibrate_near_dup_threshold(two_seeds) == _MAX_NEAR_DUP_THRESHOLD
    assert calibrate_near_dup_threshold(nine_seeds) == _MAX_NEAR_DUP_THRESHOLD
    assert calibrate_near_dup_threshold(sixteen_seeds) == _MAX_NEAR_DUP_THRESHOLD


def test_filter_keeps_candidates_well_outside_threshold():
    """Round‑29 regression test for the filter side.

    A candidate whose min‑seed distance is well above the
    threshold (i.e. not near‑duplicate) must be kept regardless
    of how many seeds are in the cluster.
    """
    # Two identical seeds.
    seed_a = _unit([1, 0, 0, 0]).tolist()
    seed_b = _unit([1, 0, 0, 0]).tolist()
    seeds = [seed_a, seed_b]

    # Two very different candidates — clearly distinct from the
    # seed cluster.
    cand1 = _unit([0, 1, 0, 0]).tolist()
    cand2 = _unit([0, 0, 1, 0]).tolist()

    threshold = calibrate_near_dup_threshold(seeds)
    mask = filter_near_duplicates([cand1, cand2], seeds, threshold)
    # Both must be kept.
    assert mask == [True, True], f"expected both kept, got {mask}"


def test_filter_drops_near_duplicate():
    """A candidate that's nearly identical to a seed must be
    dropped — that's the whole point of the filter."""
    seed = _unit([1, 0, 0, 0]).tolist()
    near_dup = _unit([0.99, 0.01, 0, 0]).tolist()  # very close to seed
    distinct = _unit([0, 1, 0, 0]).tolist()         # orthogonal

    threshold = 0.05  # tight enough that near_dup is under
    mask = filter_near_duplicates([near_dup, distinct], [seed], threshold)
    assert mask == [False, True], f"expected [False, True], got {mask}"
