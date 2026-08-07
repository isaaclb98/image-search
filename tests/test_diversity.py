"""
tests/test_diversity.py — Unit tests for search Diversity and legacy MMR.

Verifies the core algorithm, edge cases, and the embedding-normalisation
helper. Does NOT require Qdrant or a SigLIP2 model — the ranking
module accepts plain hit-like objects and small vectors.
"""

from __future__ import annotations

import math

from search.diversity import (
    DiversityResultCache,
    mmr_rerank,
    rank_diverse,
    relevance_drop_for_mode,
    resolve_depth,
    resolve_mode,
    _cosine_sim,
)


def _unit_vec(*components) -> list[float]:
    """Normalise a vector to unit length."""
    norm = math.sqrt(sum(c * c for c in components)) or 1.0
    return [c / norm for c in components]


def _make_hit(hit_id: str, score: float = 0.0, payload: dict | None = None):
    """Minimal hit-like object for testing."""
    class FakeHit:
        def __init__(self, id_, score_):
            self.id = id_
            self.score = score_
            self.payload = payload or {}
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


class TestSearchDiversity:

    def test_resolve_mode_supports_legacy_boolean(self):
        assert resolve_mode(None, False) == ("off", 0.0)
        assert resolve_mode(None, True) == ("balanced", 0.5)
        assert resolve_mode("high") == ("high", 0.88)
        assert resolve_mode("off", True) == ("off", 0.0)

    def test_resolve_depth_uses_mode_specific_auto_defaults(self):
        assert resolve_depth(None, "off") == ("auto", 0)
        assert resolve_depth("auto", "low") == ("auto", 500)
        assert resolve_depth("auto", "balanced") == ("auto", 1000)
        assert resolve_depth("auto", "high") == ("auto", 2000)
        assert resolve_depth("5000", "low") == ("5000", 5000)

    def test_resolve_depth_rejects_unknown_value(self):
        import pytest

        with pytest.raises(ValueError, match="diversity_depth must be one of"):
            resolve_depth("10000", "high")

    def test_rank_diverse_can_bound_result_count(self):
        q = _unit_vec(1, 0, 0)
        hits = [
            (_make_hit("a"), _unit_vec(1, 0, 0)),
            (_make_hit("b"), _unit_vec(0.9, 0.1, 0)),
            (_make_hit("c"), _unit_vec(0, 1, 0)),
        ]
        ranking = rank_diverse(hits, q, mode="high", max_results=2)
        assert len(ranking.hits) == 2
        assert ranking.stats.result_count == 2

    def test_rank_diverse_rejects_unrepresentable_dhash_threshold(self):
        import pytest

        with pytest.raises(ValueError, match="between 0 and 64"):
            rank_diverse([], [1.0], mode="high", duplicate_hamming_distance=65)

    def test_relevance_drop_scales_with_strength(self):
        import pytest

        assert relevance_drop_for_mode("low", 0.10) == pytest.approx(0.06)
        assert relevance_drop_for_mode("balanced", 0.10) == pytest.approx(0.10)
        assert relevance_drop_for_mode("high", 0.10) == pytest.approx(0.18)

    def test_resolve_mode_rejects_unknown_mode(self):
        import pytest

        with pytest.raises(ValueError, match="diversity must be one of"):
            resolve_mode("random")

    def test_rank_diverse_collapses_exact_duplicate_payloads(self):
        q = _unit_vec(1, 0, 0)
        hits = [
            (_make_hit("best", payload={"content_sha256": "same"}), q),
            (_make_hit("copy", payload={"content_sha256": "same"}), _unit_vec(0.99, 0.1, 0)),
            (_make_hit("different", payload={"content_sha256": "other"}), _unit_vec(0.7, 0, 0.7)),
        ]
        ranking = rank_diverse(hits, q, mode="balanced")
        assert [hit.id for hit in ranking.hits] == ["best", "different"]
        assert ranking.stats.duplicate_images_collapsed == 1
        assert ranking.stats.applied is True

    def test_rank_diverse_unions_all_transitive_dhash_matches(self):
        q = _unit_vec(1, 0, 0)
        hits = [
            (_make_hit("zero", payload={"dhash": "0000000000000000"}), q),
            (_make_hit("three", payload={"dhash": "0000000000000003"}), q),
            (_make_hit("one", payload={"dhash": "0000000000000001"}), q),
        ]
        ranking = rank_diverse(
            hits,
            q,
            mode="balanced",
            duplicate_hamming_distance=1,
        )
        assert len(ranking.hits) == 1
        assert ranking.stats.duplicate_images_collapsed == 2

    def test_rank_diverse_is_deterministic_and_keeps_relevance_first(self):
        q = _unit_vec(1, 0, 0)
        hits = [
            (_make_hit("a"), _unit_vec(1, 0, 0)),
            (_make_hit("b"), _unit_vec(0.98, 0.1, 0)),
            (_make_hit("c"), _unit_vec(0.65, 0, 0.76)),
        ]
        first = rank_diverse(hits, q, mode="high", depth="2000", pool_depth=3)
        second = rank_diverse(hits, q, mode="high", depth="2000", pool_depth=3)
        assert [hit.id for hit in first.hits] == [hit.id for hit in second.hits]
        assert first.hits[0].id == "a"
        assert first.stats.semantic_groups_covered >= 1
        assert first.stats.depth == "2000"
        assert first.stats.pool_depth == 3

    def test_diversity_cache_can_clear(self):
        cache = DiversityResultCache(ttl_seconds=60, max_entries=1)
        stats = rank_diverse([], [1.0], mode="balanced").stats
        cache.put("one", ["a"], stats)
        assert cache.get("one").hits == ("a",)
        cache.clear()
        assert cache.get("one") is None
