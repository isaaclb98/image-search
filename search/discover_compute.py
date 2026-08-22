"""
search/discover_compute.py — Pure compute for the Discovery feature.

Phase B3 (compute/IO separation): `_mmr_select` is the only pure
piece of the Discovery pipeline — it picks `k` items that
maximise relevance minus a similarity penalty to items already
chosen, so the burst pool spans the liked neighbourhood instead
of being 20 near-duplicates.

Everything else in `search/discover.py` is intrinsically
IO-coupled (qdrant.retrieve, session state mutation, IndexDB
random sampling). They stay in `search/discover.py`.

The compute function uses numpy internally for the pairwise
similarity matrix. Pure with respect to the rest of the codebase:
no Qdrant, no session state, no filesystem, no network.
"""

from __future__ import annotations

import numpy as np


def mmr_select(
    candidates: list[tuple[str, float, list[float]]],
    k: int,
    lambda_: float,
) -> list[str]:
    """Greedy Maximal Marginal Relevance.

    WHAT this is: a re-ranking that picks k items maximising
        relevance_to_query - LAMBDA * max_similarity_to_already_picked
    The first term keeps the burst pool on-topic (close to what the
    user has liked); the second term pushes each new pick away from
    ones we've already chosen, so the cached burst pool spans more
    of the liked neighbourhood instead of being 20 near-duplicates.

    `candidates` is a list of (id, relevance_score, vector) sorted
    by relevance desc. `relevance_score` is already cosine
    similarity to the liked-mean (the recommend() call returns it).
    Vectors are assumed unit-length (SigLIP2 embeddings in Qdrant
    are stored normalised, so dot product == cosine similarity).
    Falls back to the top-k by score if k >= len(candidates).
    """
    if k >= len(candidates):
        return [c[0] for c in candidates]
    if lambda_ < 0:
        raise ValueError(f"lambda_ must be non-negative (got {lambda_})")

    ids = [c[0] for c in candidates]
    scores = np.asarray([c[1] for c in candidates], dtype=np.float32)
    vecs = np.asarray([c[2] for c in candidates], dtype=np.float32)

    selected: list[int] = []
    # Pre-compute the full pairwise sim matrix once — k is small
    # (≤ session.opts.mmr_pool_size) but candidates can be up to
    # session.opts.recommend_overfetch (200). One matmul vs O(k*n) per pick.
    sim = vecs @ vecs.T  # (n, n) cosine similarities

    while len(selected) < k:
        if not selected:
            # First pick: highest relevance.
            best = int(np.argmax(scores))
        else:
            # For each remaining candidate, MMR value = its
            # relevance - LAMBDA * (max similarity to any already-
            # selected candidate). The -inf mask drops already-
            # picked ones.
            sel = np.asarray(selected, dtype=np.int32)
            max_sim_to_sel = sim[:, sel].max(axis=1)
            mmr_value = scores - lambda_ * max_sim_to_sel
            mmr_value[selected] = -np.inf
            best = int(np.argmax(mmr_value))
        selected.append(best)

    return [ids[i] for i in selected]
