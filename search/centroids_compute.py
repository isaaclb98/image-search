"""
search/centroids_compute.py — Pure compute for the centroid feature.

Phase B3 (compute/IO separation): every function in this module is
deterministic and side-effect-free. The only inputs are vectors,
weights, and a threshold; the only outputs are blended vectors,
display names, and per-candidate keep-masks. No disk IO, no
runtime state, no logging beyond debug-level invariants.

This makes the module trivially unit-testable:
- feed it hand-crafted vectors
- assert the blended result
- assert the keep-mask against expected near-duplicates

For IO concerns (.pt file loading, dynamic registry) and parsing
helpers, see `search/centroids.py` (the service surface).
"""

from __future__ import annotations

import numpy as np


def blend_centroids(
    entries: list[tuple[list[float], str]],
    weights: list[float] | None,
    expected_dim: int | None = None,
) -> list[float]:
    """Weighted mean of centroid vectors, re-normalised to unit length.

    `entries` is a list of `(vector, name)` pairs. `weights` is an
    optional list of floats with the same length; when omitted or
    shorter than `entries`, missing weights default to 1.0. When
    longer than `entries`, the extras are ignored (defensive
    against URL parsing quirks).

    If `expected_dim` is given, every input vector is checked
    against it. Mismatched dimensions raise ValueError — averaging
    a 768-dim CLIP vector with a 1536-dim SigLIP2 vector returns
    garbage scores and the failure mode is silent, so we refuse
    rather than let it through. The static centroid store already
    enforces expected_model + expected_feature_dim at load time
    and dynamic centroids share the indexer's embedding space, so
    in practice the dim check is a belt-and-braces guard for
    cross-source blends.

    Returns the blended vector as a plain Python list[float]. The
    caller decides what to label it (`wuxia+portrait`, etc.) — the
    helper doesn't impose a name shape.
    """
    if not entries:
        raise ValueError("at least one centroid is required to blend")
    n = len(entries)
    if weights is None:
        weights_list = [1.0] * n
    else:
        weights_list = list(weights)[:n]
        while len(weights_list) < n:
            weights_list.append(1.0)

    # Sum non-positive weights → zero-blend → vector collapses to
    # zero on renormalisation. Reject explicitly so the caller
    # doesn't ship a zero-vector query to Qdrant.
    total = sum(weights_list)
    if total <= 0:
        raise ValueError(
            f"centroid weights must be positive (got {weights_list})"
        )

    dim = len(entries[0][0])
    if dim == 0:
        raise ValueError("centroid vector is empty")
    if expected_dim is not None and dim != expected_dim:
        raise ValueError(
            f"centroid vector dim {dim} != expected {expected_dim} "
            f"(name={entries[0][1]!r})"
        )

    blended = [0.0] * dim
    for (vec, _name), w in zip(entries, weights_list, strict=False):
        if len(vec) != dim:
            raise ValueError(
                f"centroid {entries[0][1]!r} dim {dim} != "
                f"subsequent vector dim {len(vec)}"
            )
        for i, v in enumerate(vec):
            blended[i] += w * v

    norm_sq = sum(v * v for v in blended)
    norm = norm_sq ** 0.5
    if norm == 0:
        raise ValueError(
            "blended centroid collapsed to zero (weights sum to zero "
            "or all inputs are zero)"
        )
    inv = 1.0 / norm
    return [v * inv for v in blended]


def composite_centroid_name(names: list[str]) -> str:
    """Render a multi-centroid blend label for display.

    Stable order matches the URL `?centroid=&centroid=&centroid=`
    order. Joins with `+` so it's both readable and round-trippable
    through a single token if a future endpoint wants one.

    Two-centroid: `wuxia+portrait`.
    Three-plus:   `wuxia+portrait+landscape` (no truncation —
    UI already copes with long centroid names from .pt files).
    """
    if not names:
        return ""
    return "+".join(names)


def calibrate_near_dup_threshold(seed_vectors: list[list[float]] | None) -> float:
    """Return the cosine-distance cutoff below which a candidate
    is treated as a near-duplicate of the seed set.

    Calibration: take the 1st-percentile of the seed set's own
    pairwise cosine distances. The seed set's pairwise distances
    are the empirical "how close do two versions of the same
    photo get" scale for THIS centroid — re-encodes, crops,
    recompressions of the seed photos will have distances
    comparable to that scale (often tighter), and genuinely
    distinct photos will sit further out. Setting the cutoff at
    the 1st percentile is conservative: it only drops candidates
    tighter than the tightest typical seed-seed pair, so we
    accept some false negatives in exchange for very few false
    positives (i.e. very few "distinct" photos wrongly excluded).

    Special cases:
      - 0 seeds or 1 seed: there's no intra-cluster scale to
        calibrate against. Return 0.0 (matches everything within
        the seed cluster — which is just the seed itself).
      - All seeds identical (zero pairwise distance): the
        percentile is 0.0, same as above.
      - Non-unit-length inputs are renormalised here so a
        non-unit seed vector can't bias the calibration upward
        via dot-product collapse.

    Returned value is on the cosine-distance scale
    [0, 2] (we operate on unit-normalised embeddings, so the
    practical range is [0, 1]).
    """
    if not seed_vectors or len(seed_vectors) < 2:
        return 0.0
    seeds = np.asarray(seed_vectors, dtype=np.float32)
    if seeds.ndim != 2 or seeds.shape[0] < 2:
        return 0.0
    # Renormalise defensively. Qdrant stores unit-norm vectors
    # so this is a no-op in practice, but a test fixture or a
    # future indexer change shouldn't be able to silently break
    # the calibration.
    norms = np.linalg.norm(seeds, axis=1, keepdims=True)
    nonzero = norms > 0
    if not np.all(nonzero):
        # Drop any zero-length rows; they would NaN out the
        # calibration.
        seeds = seeds[nonzero[:, 0]]
    if seeds.shape[0] < 2:
        return 0.0
    seeds = seeds / np.linalg.norm(seeds, axis=1, keepdims=True)
    # Pairwise cosine similarities → distances.
    # sim[i, j] = seeds[i] · seeds[j] (unit vectors, so == cos).
    sim = seeds @ seeds.T
    # We only care about off-diagonal pairs. Take the upper
    # triangle (i < j) and convert to cosine distance.
    iu = np.triu_indices(seeds.shape[0], k=1)
    pairwise_sim = sim[iu]
    pairwise_dist = 1.0 - pairwise_sim
    if pairwise_dist.size == 0:
        return 0.0
    # 1st percentile of intra-seed distances = the cutoff.
    # `method='lower'` keeps the result an actual observed
    # distance (rather than interpolating between two observed
    # values, which could nudge the cutoff slightly below the
    # true tightest pair and start dropping genuinely distinct
    # neighbours). numpy 2.x renamed `interpolation` -> `method`;
    # we use the new name and let the older `interpolation`
    # keyword age out.
    return float(np.percentile(pairwise_dist, 1, method="lower"))


def filter_near_duplicates(
    candidate_vectors: list[list[float]],
    seed_vectors: list[list[float]],
    threshold: float,
) -> list[bool]:
    """Return a per-candidate keep-mask for Layer 2.

    `keep[i] = True` means candidate `i` is NOT a near-duplicate of
    the seed cluster (i.e. it's far enough from every seed that
    it represents a distinct result).

    `keep[i] = False` means candidate `i` is too close to the
    seed cluster to be a useful result — the user has already
    seen the seeds.

    The threshold comes from `calibrate_near_dup_threshold`: it's
    the 1st-percentile of the seed set's own pairwise distances.
    A candidate is "near-dup" iff its minimum cosine distance to
    any seed is BELOW this threshold (i.e. tighter than the
    tightest typical seed-seed pair).
    """
    n_cand = len(candidate_vectors)
    if not n_cand or not seed_vectors:
        return [True] * n_cand
    cand = np.asarray(candidate_vectors, dtype=np.float32)
    seeds = np.asarray(seed_vectors, dtype=np.float32)
    if cand.ndim != 2 or cand.shape[1] != seeds.shape[1]:
        # Defensive: shape mismatch should never happen (both
        # come from the same collection) but a silent bug here
        # would manifest as "everything kept" or "everything
        # dropped" with no log. Surface it loudly instead.
        raise ValueError(
            f"candidate dim {cand.shape[1] if cand.ndim == 2 else '?'} "
            f"!= seed dim {seeds.shape[1] if seeds.ndim == 2 else '?'}"
        )
    # (n_seeds, n_candidates) cosine similarity matrix. Unit
    # inputs, so this is just the dot product.
    sim = seeds @ cand.T
    # Per-candidate minimum distance: 1 - max sim across seeds.
    # `max` is what we want — a candidate is "close to the seed
    # cluster" if ANY seed is close.
    min_dist = 1.0 - sim.max(axis=0)
    return [(d >= threshold) for d in min_dist.tolist()]
