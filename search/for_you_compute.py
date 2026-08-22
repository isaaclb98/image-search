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
