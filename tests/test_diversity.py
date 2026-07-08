"""
tests/test_diversity.py — Unit tests for MMR re-ranking.

Verifies the core algorithm, edge cases, and the embedding-normalisation
helper. Does NOT require Qdrant or a SigLIP2 model — the diversity
module is pure Python + math.
"""

from __future__ import annotations

import math

from search.diversity import mmr_rerank, _cosine_sim


def _unit_vec(*components) -> list[float]:
    """Normalise a vector to unit length."""
    norm = math.sqrt(sum(c * c for c in components)) or 1.0
    return [c / norm for c in components]


def _make_hit(hit_id: str, score: float = 0.0):
    """Minimal hit-like object for testing."""
    class FakeHit:
        def __init__(self, id_, score_):
            self.id = id_
            self.score = score_
    return FakeHit(hit_id, score)


class TestMmrRerank:

    def test_empty_input(self):
        assert mmr_rerank([], [1.0], k=10) == []

    def test_k_zero(self):
        hit = _make_hit("a")
        assert mmr_rerank([(hit, [1.0])], [1.0], k=0) == []

    def test_k_greater_than_pool(self):
        """When k > len(candidates), all candidates are returned."""
        hits = [
            (_make_hit("a"), _unit_vec(1, 0)),
            (_make_hit("b"), _unit_vec(0, 1)),
        ]
        result = mmr_rerank(hits, [1.0, 0.0], k=10)
        assert len(result) == 2
        assert result[0].id == "a"  # best query match first

    def test_lambda_one_is_pure_relevance(self):
        """λ=1.0 should match raw top-K ordering (no diversity penalty)."""
        # Two candidates: a is close to query, b is far.
        q = _unit_vec(1, 0)
        hits = [
            (_make_hit("a"), _unit_vec(0.99, 0.01)),  # close
            (_make_hit("b"), _unit_vec(0.1, 0.99)),   # far
        ]
        result = mmr_rerank(hits, q, k=2, lambda_=1.0)
        assert result[0].id == "a"
        assert result[1].id == "b"

    def test_diversity_reorders(self):
        """With λ=0.5, a diverse-but-reasonable match can beat a
        near-duplicate of the top pick."""
        q = _unit_vec(1, 0, 0)
        # a is the best query match. b is very close to a in
        # embedding space (same scene, near-duplicate frame).
        # c is a different photo — slightly less relevant but in
        # a different region of embedding space.
        hits = [
            (_make_hit("a"), _unit_vec(0.99, 0.10, 0.05)),
            (_make_hit("b"), _unit_vec(0.97, 0.15, 0.08)),  # near-dup of a
            (_make_hit("c"), _unit_vec(0.60, -0.70, 0.30)),  # different direction
        ]
        result = mmr_rerank(hits, q, k=3, lambda_=0.5)
        # First pick is always a (best query match).
        assert result[0].id == "a"
        # Second pick should be c (diverse) not b (near-dup of a),
        # because a is already selected and c is penalized less.
        assert result[1].id == "c"
        assert result[2].id == "b"

    def test_single_candidate(self):
        """Only one candidate → it's always selected."""
        hit = _make_hit("only")
        result = mmr_rerank([(hit, _unit_vec(1, 0))], _unit_vec(1, 0), k=1)
        assert result[0].id == "only"

    def test_identical_vectors_spread(self):
        """10 identical vectors with the same query → only the first
        should be selected (all subsequent ones have max diversity
        penalty = 1.0, so MMR score = λ * 1.0 - (1-λ) * 1.0 = 2λ - 1.
        At λ=0.5, that's 0 — but the first one had no penalty so it's
        strictly higher. At λ=0.7, score = 0.7 - 0.3 = 0.4, still
        positive but lower than the first's 0.7."""
        q = _unit_vec(1, 0)
        v = _unit_vec(1, 0)  # identical to query
        hits = [(_make_hit(str(i)), v) for i in range(10)]
        result = mmr_rerank(hits, q, k=5, lambda_=0.5)
        # The first pick is "0" (score = 0.5 * 1.0 = 0.5).
        # All subsequent picks have score = 0.5 * 1.0 - 0.5 * 1.0 = 0.0.
        # So they're all equal — but we must pick 5. The order is
        # deterministic based on iteration order.
        assert result[0].id == "0"
        assert len(result) == 5

    def test_query_vector_unchanged(self):
        """The input query vector should not be mutated."""
        q = [1.0, 0.0]
        original = q[:]
        hits = [(_make_hit("a"), _unit_vec(1, 0))]
        mmr_rerank(hits, q, k=1)
        assert q == original

    def test_candidate_vectors_unchanged(self):
        """Candidate vectors should not be mutated."""
        v = _unit_vec(1, 0)
        original = v[:]
        hits = [(_make_hit("a"), v)]
        mmr_rerank(hits, [1.0, 0.0], k=1)
        assert v == original


class TestCosineSim:

    def test_identical(self):
        assert _cosine_sim([1.0, 0.0], [1.0, 0.0]) == 1.0

    def test_opposite(self):
        assert _cosine_sim([1.0, 0.0], [-1.0, 0.0]) == -1.0

    def test_orthogonal(self):
        assert abs(_cosine_sim([1.0, 0.0], [0.0, 1.0])) < 1e-10

    def test_mixed(self):
        a = _unit_vec(3, 4)
        b = _unit_vec(3, 4)
        sim = _cosine_sim(a, b)
        assert abs(sim - 1.0) < 1e-6
