"""
search/diversity.py — MMR (Maximal Marginal Relevance) re-ranking.

Provides a single function, ``mmr_rerank``, that takes a list of
(SearchHit, vector) pairs and returns a diversified subset. Uses
the stored embedding vectors directly as the diversity signal — no
perceptual hashing, no clustering, no extra Qdrant queries beyond
a single batch vector fetch.

Design rationale
----------------
The search endpoint returns raw top-K by cosine similarity to the
query vector. When many near-identical images rank highly (burst
shots, same scene different angle, same aesthetic), the result
list is homogeneous. MMR addresses this by penalizing each
candidate's relevance score by its similarity to already-selected
results:

    score(item) = λ · sim(item, query)
                  - (1 − λ) · max_{j ∈ selected} sim(item, j)

At λ = 1.0, MMR is a no-op (pure relevance). At λ = 0.5, relevance
and diversity are equally weighted. The first pick is always the
best query match (the diversity penalty is zero for the first
iteration).

The diversity signal is the **embedding cosine distance** — the same
SigLIP2 vectors stored in Qdrant. This catches any kind of semantic
similarity: same scene, same colour palette, same pose, same
composition. No separate perceptual hash is needed because the
embedding is a richer signal (a burst of 10 nearly-identical frames
will have near-identical embeddings, so MMR naturally spreads them).

Cost
----
- One batch vector fetch from Qdrant (retrieve_batch_with_vectors).
- O(N²) pairwise cosine comparisons on the server side. For N=200
  (the typical candidate pool), this is ~20k dot products — sub-5ms
  with numpy, ~50ms with pure Python. Acceptable for a personal tool.
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def mmr_rerank(
    hits_with_vectors: list[tuple],
    query_vector: list[float],
    k: int,
    lambda_: float = 0.5,
) -> list:
    """
    Re-rank hits using Maximal Marginal Relevance over embedding vectors.

    Parameters
    ----------
    hits_with_vectors:
        List of ``(hit, vector)`` tuples where ``hit`` is any object
        with an ``id`` attribute (typically a ``SearchHit``) and
        ``vector`` is a ``list[float]`` from Qdrant.
    query_vector:
        The original query embedding (L2-normalised unit vector).
    k:
        Number of results to return. When ``len(hits) <= k``, all hits
        are returned (diversity is still applied to the ordering).
    lambda_:
        Relevance-diversity trade-off in [0, 1].

        - 1.0 — pure relevance (no diversity, same as raw top-K).
        - 0.7 — default. Tilts toward relevance but spreads things
          out noticeably.
        - 0.5 — equal weight. The #2 result is often surprising.
        - 0.0 — pure diversity (probably not useful).

    Returns
    -------
    list
        The same hit objects (no vector data), re-ordered by MMR
        score descending. Length ``min(k, len(hits_with_vectors))``.
    """
    if not hits_with_vectors or k <= 0:
        return [h for h, _v in hits_with_vectors[:k]]

    # Normalise query vector once.
    qv = _as_float_list(query_vector)

    selected: list[tuple] = []
    candidates = list(hits_with_vectors)

    while len(selected) < k and candidates:
        best_idx = 0
        best_score = -float("inf")

        for i, (hit, vec) in enumerate(candidates):
            cv = _as_float_list(vec)
            q_sim = _cosine_sim(qv, cv)

            if selected:
                # Diversity penalty: max similarity to any selected item.
                max_sim = max(
                    _cosine_sim(cv, _as_float_list(sv))
                    for _sh, sv in selected
                )
            else:
                max_sim = 0.0

            score = lambda_ * q_sim - (1.0 - lambda_) * max_sim
            if score > best_score:
                best_score = score
                best_idx = i

        selected.append(candidates.pop(best_idx))

    return [h for h, _v in selected]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Dot product between two unit-norm vectors.

    Assumes both are already L2-normalised (SigLIP2 embeddings are
    always unit-norm). Returns ``sum(ai * bi)``.
    """
    s = 0.0
    for ai, bi in zip(a, b):
        s += ai * bi
    return s


def _as_float_list(v) -> list[float]:
    """Normalise a vector to ``list[float]``.

    Qdrant may return numpy arrays or lists depending on the client
    version. This normalises both to flat Python lists.
    """
    if hasattr(v, "tolist"):
        return v.tolist()
    return list(v)
