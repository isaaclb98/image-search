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


# Round‑34: default K for sample-centroid retrieval. Picked as a
# small enough fraction of typical "Likes" sets (50–500) that the
# subset is genuinely informative, but large enough that the mean
# of K is still a meaningful centroid. 10 is the constant the
# product spec calls out; routes can override per-request.
#
# Round‑75: K is now the number of *clusters* (k-means K), not the
# number of *photos* in a random subset. We pick N_CLUSTERS=3 of
# those clusters per request, so each "Surprise me" refresh
# surfaces a different blend of visual modes instead of a noisy
# 10-photo mean. K=10 stays; the new param is the sample_n=3 knob.
DEFAULT_SAMPLE_K = 10
DEFAULT_CLUSTER_SAMPLE_N = 3


def sample_centroid(
    seed_ids: list[str],
    vectors: list[list[float]],
    k: int = DEFAULT_SAMPLE_K,
    *,
    seed: int | None = None,
) -> tuple[list[float], int, list[str]]:
    """Pick a random K-subset of (seed_ids, vectors) and return its mean.

    Returns `(centroid, picked_count, picked_seed_ids)`:
      - `centroid`: unit-length mean of the K selected vectors,
        ready to be passed straight to a Qdrant cosine search.
      - `picked_count`: how many vectors actually contributed
        (≤ k, ≤ len(vectors)). Equals len(vectors) when the
        input is shorter than k — see fallback note.
      - `picked_seed_ids`: the point ids of the selected vectors,
        in the same order as the rows of the centroid. The route
        uses this for the `must_not` exclude-ids filter so the
        results don't echo the sample back at the user, and the
        frontend can render the "based on N random photos" copy
        with the actual list.

    Fallback: when `len(vectors) <= k` (typical for very small
    albums, ≤ k likes) we don't over-sample with replacement —
    sample_with_replacement would weight duplicates and skew
    the mean. The whole input is used instead and `picked_count`
    reflects that. This keeps the behaviour consistent with
    "sample mode" being a no-op for tiny populations, rather
    than a different algorithm.

    Determinism: pass `seed=int` to make the selection
    reproducible (used by unit tests). Without a seed we use
    the module-level `random` RNG, which is fine for production
    where each request wants a fresh sample.
    """
    n = len(vectors)
    if n == 0:
        raise ValueError("sample_centroid requires at least one vector")
    if k <= 0:
        raise ValueError(f"k must be > 0, got {k}")
    if len(seed_ids) != n:
        raise ValueError(
            f"seed_ids/vectors length mismatch: "
            f"{len(seed_ids)} ids vs {n} vectors"
        )

    import random

    if n <= k:
        # Fallback: use the whole input. Don't dedupe — the
        # caller already passed a unique-id list.
        chosen_indices = list(range(n))
    else:
        rng = random.Random(seed) if seed is not None else random  # noqa: S311
        # sample(population, k) without replacement — equivalent
        # to random.sample(range(n), k) but spelled out so the
        # intent is obvious in code review.
        chosen_indices = rng.sample(range(n), k)

    selected_vecs = [vectors[i] for i in chosen_indices]
    selected_ids = [seed_ids[i] for i in chosen_indices]

    arr = np.asarray(selected_vecs, dtype=np.float32)
    if arr.ndim != 2:
        # Defensive: callers should pass list[list[float]].
        raise ValueError(
            f"expected 2D vector matrix, got shape {arr.shape}"
        )
    centroid = arr.mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm == 0:
        # Should be impossible with real embeddings, but if a
        # caller passes all-zero vectors we'd otherwise return
        # a zero vector that Qdrant silently ranks at 0.0.
        # Surface it instead.
        raise ValueError(
            "sample centroid collapsed to zero — all input vectors "
            "appear to be zero"
        )
    centroid = (centroid / norm).tolist()
    return (centroid, len(chosen_indices), selected_ids)


def cluster_then_sample_centroid(
    seed_ids: list[str],
    vectors: list[list[float]],
    k: int = DEFAULT_SAMPLE_K,
    n: int = DEFAULT_CLUSTER_SAMPLE_N,
    *,
    seed: int | None = None,
) -> tuple[list[float], int, list[str]]:
    """Cluster the seed set into K groups, then mean N of those centroids.

    Round‑75: replaces the previous "mean of K random photos"
    approach. A 10-photo random subset of a 200-photo album is
    ~5% sampling noise, dominated by whichever visual mode
    happens to land in the random 10. Each refresh re-rolled a
    noisy centroid that mostly reflected sampling luck.

    The new approach picks representatives, not photos:
      1. Cluster the album's photos into K=10 groups via k-means
         on the embeddings. Each cluster is one visual mode of
         the album (e.g. "outdoor daytime", "stage lighting").
      2. Each cluster centroid is averaged across all members of
         that cluster — much more stable than any single photo.
      3. Uniform-randomly pick N=3 cluster centroids.
      4. Return the mean of those N centroids as the query vector.

    Each refresh picks a different 3-of-10 → a different blend
    of visual modes, with much less per-refresh variance than
    the photo-subsampling approach.

    Args:
      seed_ids: ids of the source photos (one per vector).
      vectors:  D-dim unit-norm embeddings.
      k:        number of clusters (k-means K). Default 10.
      n:        number of clusters to average per request.
                Default 3. Must satisfy 1 <= n <= k.
      seed:     optional int for deterministic selection
                (used by unit tests).

    Returns:
      (centroid, picked_count, picked_seed_ids):
        - centroid: unit-length D-dim vector (the sub-centroid).
        - picked_count: number of clusters selected (= n, or fewer
                        on small inputs — see fallback).
        - picked_seed_ids: the seed ids that contributed to the
                           picked clusters. Used by the route as
                           the exclude-list so results don't echo
                           the sample back at the user.

    Fallbacks:
      - len(vectors) == 0: raises ValueError.
      - len(vectors) <= k: skip clustering (k-means needs at
        least k distinct points); fall back to sample_centroid
        with the whole input as the "subset". Behaviour matches
        the previous implementation for tiny populations.
      - n > k: raise ValueError (caller misconfigured).
      - len(vectors) < n: clip n down to len(vectors).

    Determinism: pass `seed=int` to make the cluster
    selection reproducible. The k-means clustering itself is
    not seeded by default — each call gets a fresh partition.
    Tests that need a fixed partition should pass `seed` for
    both k-means init and the cluster picker (TODO: if the
    unit test ever needs that, plumb the seed through; current
    tests verify the API shape, not specific partitions).
    """
    n_vecs = len(vectors)
    if n_vecs == 0:
        raise ValueError("cluster_then_sample_centroid requires at least one vector")
    if k <= 0:
        raise ValueError(f"k must be > 0, got {k}")
    if n <= 0:
        raise ValueError(f"n must be > 0, got {n}")
    if len(seed_ids) != n_vecs:
        raise ValueError(
            f"seed_ids/vectors length mismatch: "
            f"{len(seed_ids)} ids vs {n_vecs} vectors"
        )
    if n > k:
        raise ValueError(
            f"n ({n}) must be <= k ({k}); cannot pick more clusters than exist"
        )

    # Fallback: tiny input — cluster into at most as many groups
    # as we have points. k-means needs K <= N; when N is small,
    # one point per cluster trivially gives us the whole-input
    # mean. Match the existing sample_centroid fallback for
    # tiny populations so "sample mode" stays a no-op there.
    if n_vecs <= k:
        # Degenerate case: every point is its own cluster. The
        # sub-centroid is the mean of all N_CLUSTERS points,
        # which equals the album's full mean. Effectively turns
        # sample mode into centroid mode for very small albums.
        return sample_centroid(seed_ids, vectors, k=n_vecs, seed=seed)

    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim != 2:
        raise ValueError(
            f"expected 2D vector matrix, got shape {arr.shape}"
        )

    # ---- k-means ----
    # Lloyd's algorithm, written in numpy. K=10, N=200 typical
    # for a Likes album — converges in ~20 iterations, well
    # under 5ms on a single core. No sklearn dependency.
    #
    # Init: random K points from the input (Forgy method). Stable
    # enough for our small K / modest-N regime and avoids the
    # numerical foot-guns of k-means++ on unit vectors.
    import random

    rng = random.Random(seed) if seed is not None else random  # noqa: S311
    init_indices = rng.sample(range(n_vecs), k)
    centroids = arr[init_indices].copy()  # shape (k, d)

    # Iterations cap is generous; Lloyd's typically converges in
    # under 30 iterations for K=10, N=200 on real embeddings. The
    # delta-tolerance below also exits early when stable.
    max_iter = 50
    tol = 1e-4
    labels = np.zeros(n_vecs, dtype=np.int64)
    for _ in range(max_iter):
        # Assign each point to nearest centroid (cosine ==
        # dot product on unit vectors, so no normalization step
        # needed — embeddings are L2-normalized at index time).
        sims = arr @ centroids.T  # (n, k)
        new_labels = np.argmax(sims, axis=1)

        # Recompute centroids as the mean of assigned points.
        new_centroids = np.zeros_like(centroids)
        for c in range(k):
            mask = new_labels == c
            if mask.any():
                new_centroids[c] = arr[mask].mean(axis=0)
            else:
                # Empty cluster — keep the old centroid. Lloyd's
                # with random init can hit this when two initial
                # centroids start in the same neighbourhood and
                # one starves the other. Re-seeding would be
                # cleaner, but in practice K=10 vs N=200 doesn't
                # hit this often enough to matter.
                new_centroids[c] = centroids[c]

        shift = float(np.linalg.norm(new_centroids - centroids))
        centroids = new_centroids
        if np.array_equal(new_labels, labels) or shift < tol:
            labels = new_labels
            break
        labels = new_labels

    # ---- pick N cluster centroids ----
    # n_clip handles the case where the cluster count came out
    # smaller than n (shouldn't happen with K<=N and no empty
    # clusters, but defensive against edge cases).
    n_pick = min(n, k)
    picked_cluster_ids = rng.sample(range(k), n_pick)

    picked_centroids = centroids[picked_cluster_ids]  # (n_pick, d)
    # Map each picked cluster back to its member seed ids. The
    # route uses this for the exclude list (so results don't
    # echo the sample).
    picked_seed_ids: list[str] = []
    for c in picked_cluster_ids:
        members = [seed_ids[i] for i in range(n_vecs) if labels[i] == c]
        picked_seed_ids.extend(members)

    # ---- mean the picked centroids ----
    sub = picked_centroids.mean(axis=0)
    norm = float(np.linalg.norm(sub))
    if norm == 0:
        # Numerically impossible with real embeddings (each
        # centroid is the mean of non-empty L2-normalized
        # vectors, so it's not the zero vector). Surface rather
        # than return a zero vector Qdrant ranks at 0.0.
        raise ValueError(
            "cluster_then_sample_centroid collapsed to zero"
        )
    sub = (sub / norm).tolist()
    return (sub, n_pick, picked_seed_ids)


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
