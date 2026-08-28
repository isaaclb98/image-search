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

    Round‑29 fix: use a fixed conservative threshold of
    `_MAX_NEAR_DUP_THRESHOLD` (0.02). The previous implementation
    derived the cutoff from the seed set's own pairwise distance
    distribution, which broke in two opposite ways:

    - Small seed sets (1‑9 photos): the 1st percentile of the
      pairwise distances picked the tightest pair, producing a
      cutoff of ~0.12 ("moderately similar", not "near‑duplicate")
      that filtered almost every candidate, leaving the user
      with an empty results page.

    - Larger seed sets (16 photos of favourites): the median
      pairwise distance was 0.14, so any candidate close to the
      favourites cluster (which is the *entire point* of the
      favourites search — the user wants photos similar to their
      favourites) was incorrectly classified as "near‑duplicate"
      and dropped.

    The right semantic is "near‑duplicate of an actual seed photo"
    — meaning an exact or near‑exact re‑encode of the same source.
    0.02 in cosine distance on SigLIP2 embeddings is roughly that:
    a JPEG re‑encode typically moves a vector by ~0.02–0.05, while
    semantically similar photos (same subject, different shot) are
    0.1+ apart.

    Special cases:
      - 0 seeds or 1 seed: there's no seed set to be near‑a‑dup
        of. Return 0.0 (no-op since filter_near_duplicates also
        short-circuits to "keep all" for empty seed sets).

    Returned value is on the cosine-distance scale [0, 2].
    """
    if not seed_vectors or len(seed_vectors) < 1:
        return 0.0
    return _MAX_NEAR_DUP_THRESHOLD


# Round‑29: fixed conservative threshold for
# `calibrate_near_dup_threshold`. 0.02 in cosine distance on
# SigLIP2 means "essentially identical re-encoding". Larger values
# (0.1+) catch semantically similar photos, which is the OPPOSITE
# of what the user wants on a favourites / album search.
_MAX_NEAR_DUP_THRESHOLD = 0.02


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
