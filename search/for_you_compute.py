"""
search/for_you_compute.py — Pure helpers for the for-you feed.

Phase B3 (compute/IO separation): the `rank()` orchestrator in
`search/for_you.py` is intrinsically IO-coupled (qdrant.recommend +
search_with_vectors), so it stays where it is. The small pure
helpers it uses — the zero-vector placeholder + the candidate-pool
size calculation — live here as functions of pure inputs.

Nothing in this module touches qdrant, the registry singleton, or
the filesystem. The `_zero_vector` function takes the model dim as
an argument (rather than reaching into the registry) so callers
control the source of truth for the dim.
"""

from __future__ import annotations


def zero_vector(feature_dim: int) -> list[float]:
    """Zero vector of the active model's dim.

    Used as a placeholder for the cold-start / diversity-rerank
    query paths that don't have a real query vector.

    `feature_dim` is passed in explicitly rather than read from
    the model registry — the caller decides which model's dim to
    use, and tests can supply a small constant without spinning
    up the full registry.
    """
    if feature_dim <= 0:
        raise ValueError(f"feature_dim must be positive (got {feature_dim})")
    return [0.0] * feature_dim


def pool_k_default(limit: int) -> int:
    """Default candidate-pool size for the diversity rerank step.

    The for-you pipeline calls qdrant.recommend for `pool_k`
    candidates, then runs the diversity rerank on top to pick the
    final `limit`. The default of `limit * 4` (min 80) gives
    diversity real headroom without overshooting Qdrant's cheap
    recommend path.
    """
    if limit <= 0:
        raise ValueError(f"limit must be positive (got {limit})")
    return max(limit * 4, 80)


def explore_pool_size(library_size: int, top_pct: float) -> int:
    """Pool size for the shuffled-for-you feed: top `top_pct`% of library.

    Computes `round(library_size × top_pct / 100)` with a floor of 1
    so an empty library doesn't produce a degenerate 0-size pool.
    No ceiling (per Isaac: "no pool ceiling"). A `top_pct=5` over
    an 800k library will fetch 40k candidates at ~5-10s; the
    caller is expected to budget that latency.

    Pure function of two numbers. Lives here (not in
    shuffled_for_you.py) so tests can pin the math without
    spinning up the qdrant fixture.
    """
    if library_size < 0:
        raise ValueError(f"library_size must be non-negative (got {library_size})")
    if top_pct <= 0 or top_pct > 100:
        raise ValueError(f"top_pct must be in (0, 100] (got {top_pct})")
    return max(round(library_size * top_pct / 100), 1)
