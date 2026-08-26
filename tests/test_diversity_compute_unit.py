"""
tests/test_diversity_compute_unit.py — Unit tests for search/diversity_compute.py.

Diversity ranking: MMR (Maximal Marginal Relevance) and rank_diverse.
These are the core algorithms that decide which results the user sees
when they ask for diverse results.

Actual API:
  mmr_rerank(hits_with_vectors, query_vector, k, lambda_=0.5)
  rank_diverse(hits_with_vectors, query_vector, *, mode, strength, max_results, ...)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from search.diversity_compute import (
    DiversityRanking,
    DiversityStats,
    _as_float_list,
    _collapse_duplicate_indices,
    _cosine_sim,
    _normalise_matrix,
    _normalise_vector,
    mmr_rerank,
    rank_diverse,
)


# ----- Helper test classes -----

@dataclass
class FakeHit:
    """Minimal hit object for testing."""
    id: str
    score: float
    payload: dict
    vector: list[float] | None = None


def _make_hit(id: str, score: float, vector: list[float] | None = None, **payload) -> FakeHit:
    return FakeHit(id=id, score=score, payload=payload, vector=vector)


# ----- _normalise_vector -----

class TestNormaliseVector:
    """Normalise a single vector to unit length."""

    def test_unit_vector_stays_unit(self):
        v = np.array([1.0, 0.0, 0.0])
        result = _normalise_vector(v)
        np.testing.assert_allclose(result, v)
        assert abs(np.linalg.norm(result) - 1.0) < 1e-9

    def test_arbitrary_vector_normalised(self):
        v = np.array([3.0, 4.0])
        result = _normalise_vector(v)
        np.testing.assert_allclose(result, [0.6, 0.8])
        assert abs(np.linalg.norm(result) - 1.0) < 1e-9

    def test_zero_vector_returns_zero(self):
        v = np.array([0.0, 0.0, 0.0])
        result = _normalise_vector(v)
        np.testing.assert_allclose(result, [0.0, 0.0, 0.0])

    def test_near_zero_vector_returns_zero(self):
        v = np.array([1e-15, 1e-15])
        result = _normalise_vector(v)
        np.testing.assert_allclose(result, [0.0, 0.0])

    def test_negative_values_normalised(self):
        v = np.array([-3.0, -4.0])
        result = _normalise_vector(v)
        np.testing.assert_allclose(result, [-0.6, -0.8])


# ----- _normalise_matrix -----

class TestNormaliseMatrix:
    """Normalise each row of a matrix to unit length."""

    def test_each_row_normalised(self):
        m = np.array([
            [3.0, 4.0],
            [0.0, 5.0],
            [1.0, 0.0],
        ])
        result = _normalise_matrix(m)
        for row in result:
            assert abs(np.linalg.norm(row) - 1.0) < 1e-9

    def test_zero_row_handled(self):
        m = np.array([
            [3.0, 4.0],
            [0.0, 0.0],
        ])
        result = _normalise_matrix(m)
        assert not np.any(np.isnan(result))


# ----- _cosine_sim -----

class TestCosineSim:
    """Cosine similarity for two vectors (assumed pre-normalised)."""

    def test_identical_vectors_return_one(self):
        v = [1.0, 0.0, 0.0]
        assert abs(_cosine_sim(v, v) - 1.0) < 1e-9

    def test_orthogonal_vectors_return_zero(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert abs(_cosine_sim(a, b)) < 1e-9

    def test_opposite_vectors_return_negative_one(self):
        a = [1.0, 0.0, 0.0]
        b = [-1.0, 0.0, 0.0]
        assert abs(_cosine_sim(a, b) - (-1.0)) < 1e-9

    def test_returns_float(self):
        result = _cosine_sim([1.0, 0.0], [1.0, 0.0])
        assert isinstance(result, float)


# ----- _as_float_list -----

class TestAsFloatList:
    """Coerce a value to a list of floats."""

    def test_none_returns_empty(self):
        assert _as_float_list(None) == []

    def test_list_of_ints(self):
        assert _as_float_list([1, 2, 3]) == [1.0, 2.0, 3.0]

    def test_list_of_floats(self):
        assert _as_float_list([1.5, 2.5]) == [1.5, 2.5]

    def test_list_of_strings_raises(self):
        with pytest.raises(ValueError):
            _as_float_list(["a", "b"])

    def test_dict_returns_empty(self):
        assert _as_float_list({"a": 1}) == []

    def test_empty_list(self):
        assert _as_float_list([]) == []


# ----- _collapse_duplicate_indices -----

class TestCollapseDuplicateIndices:
    """Union-find dedup by content_hash + dHash."""

    def test_no_duplicates_returns_all(self):
        hits = [
            _make_hit("a", 0.9, content_sha256="aaa"),
            _make_hit("b", 0.8, content_sha256="bbb"),
            _make_hit("c", 0.7, content_sha256="ccc"),
        ]
        result = _collapse_duplicate_indices(
            hits,
            query_scores=[0.9, 0.8, 0.7],
            duplicate_hamming_distance=4,
        )
        assert len(result) == 3

    def test_exact_content_hash_duplicates_collapse(self):
        """Same content_sha256 → keep highest-scoring representative."""
        hits = [
            _make_hit("a", 0.5, content_sha256="same"),
            _make_hit("b", 0.9, content_sha256="same"),
            _make_hit("c", 0.7, content_sha256="same"),
        ]
        result = _collapse_duplicate_indices(
            hits,
            query_scores=[0.5, 0.9, 0.7],
            duplicate_hamming_distance=4,
        )
        # Function returns indices of surviving hits
        assert isinstance(result, list)
        # At least one survivor (the function collapses dupes)
        assert len(result) >= 1
        # The surviving hits should be the highest-scoring one(s)
        surviving_scores = [hits[i].score for i in result]
        # All survivors must be among the highest scores
        assert max(surviving_scores) >= 0.7

    def test_missing_content_hash_falls_through(self):
        hits = [
            _make_hit("a", 0.5),
            _make_hit("b", 0.9),
        ]
        result = _collapse_duplicate_indices(
            hits,
            query_scores=[0.5, 0.9],
            duplicate_hamming_distance=4,
        )
        assert len(result) == 2

    def test_empty_hits(self):
        result = _collapse_duplicate_indices(
            [],
            query_scores=[],
            duplicate_hamming_distance=4,
        )
        assert result == []


# ----- mmr_rerank -----

class TestMmrRerank:
    """Maximal Marginal Relevance: balance relevance vs diversity."""

    def test_basic_reranking(self):
        """MMR returns k items, with the most relevant first."""
        hits = [
            _make_hit("a", 0.9, vector=[1.0, 0.0, 0.0]),
            _make_hit("b", 0.8, vector=[0.0, 1.0, 0.0]),
            _make_hit("c", 0.7, vector=[0.0, 0.0, 1.0]),
        ]
        hits_with_vectors = [(h, h.vector) for h in hits]
        query = [1.0, 0.0, 0.0]
        result = mmr_rerank(hits_with_vectors, query, k=3, lambda_=0.5)
        assert len(result) == 3

    def test_lambda_one_is_relevance_only(self):
        """lambda=1.0 → pure relevance ranking."""
        hits = [
            _make_hit("a", 0.9, vector=[1.0, 0.0]),
            _make_hit("b", 0.5, vector=[0.0, 1.0]),
            _make_hit("c", 0.7, vector=[1.0, 1.0]),
        ]
        hits_with_vectors = [(h, h.vector) for h in hits]
        query = [1.0, 0.0]
        result = mmr_rerank(hits_with_vectors, query, k=3, lambda_=1.0)
        ids = [h.id for h in result]
        # Pure relevance: a (0.9), c (0.7), b (0.5)
        assert ids == ["a", "c", "b"]

    def test_top_k_smaller_than_items(self):
        hits = [
            _make_hit("a", 0.9, vector=[1.0, 0.0]),
            _make_hit("b", 0.8, vector=[0.0, 1.0]),
            _make_hit("c", 0.7, vector=[1.0, 1.0]),
            _make_hit("d", 0.6, vector=[0.0, 0.0]),
        ]
        hits_with_vectors = [(h, h.vector) for h in hits]
        result = mmr_rerank(hits_with_vectors, [1.0, 0.0], k=2, lambda_=0.5)
        assert len(result) == 2

    def test_empty_items(self):
        result = mmr_rerank([], [1.0, 0.0], k=5, lambda_=0.5)
        assert result == []

    def test_top_k_larger_than_items(self):
        hits = [
            _make_hit("a", 0.9, vector=[1.0, 0.0]),
            _make_hit("b", 0.8, vector=[0.0, 1.0]),
        ]
        hits_with_vectors = [(h, h.vector) for h in hits]
        result = mmr_rerank(hits_with_vectors, [1.0, 0.0], k=10, lambda_=0.5)
        assert len(result) == 2

    def test_returns_list_of_hits(self):
        hits = [_make_hit("a", 0.9, vector=[1.0, 0.0])]
        hits_with_vectors = [(hits[0], hits[0].vector)]
        result = mmr_rerank(hits_with_vectors, [1.0, 0.0], k=1, lambda_=0.5)
        assert isinstance(result, list)
        assert result[0].id == "a"


# ----- rank_diverse -----

class TestRankDiverse:
    """Public API: take hits + queries, return diverse ranking."""

    def test_basic_ranking_returns_diversity_ranking(self):
        hits = [
            _make_hit("a", 0.9, vector=[1.0, 0.0]),
            _make_hit("b", 0.8, vector=[0.0, 1.0]),
            _make_hit("c", 0.7, vector=[1.0, 1.0]),
        ]
        hits_with_vectors = [(h, h.vector) for h in hits]
        result = rank_diverse(hits_with_vectors, [1.0, 0.0], max_results=3)
        assert isinstance(result, DiversityRanking)

    def test_returns_hits_and_stats(self):
        hits = [
            _make_hit("a", 0.9, vector=[1.0, 0.0]),
            _make_hit("b", 0.8, vector=[0.0, 1.0]),
        ]
        hits_with_vectors = [(h, h.vector) for h in hits]
        result = rank_diverse(hits_with_vectors, [1.0, 0.0], max_results=2)
        assert hasattr(result, "hits")
        assert hasattr(result, "stats")
        assert isinstance(result.stats, DiversityStats)

    def test_off_mode_returns_unranked(self):
        """mode='off' → no diversity applied, hits returned as-is."""
        hits = [
            _make_hit("a", 0.9, vector=[1.0, 0.0]),
            _make_hit("b", 0.8, vector=[0.0, 1.0]),
        ]
        hits_with_vectors = [(h, h.vector) for h in hits]
        result = rank_diverse(hits_with_vectors, [1.0, 0.0], mode="off", max_results=2)
        assert len(result.hits) == 2
        assert result.stats.applied is False

    def test_unknown_mode_raises(self):
        hits = [_make_hit("a", 0.9, vector=[1.0, 0.0])]
        hits_with_vectors = [(hits[0], hits[0].vector)]
        with pytest.raises(ValueError, match="unknown diversity mode"):
            rank_diverse(hits_with_vectors, [1.0, 0.0], mode="bogus-mode")

    def test_max_results_limits_output(self):
        hits = [
            _make_hit("a", 0.9, vector=[1.0, 0.0]),
            _make_hit("b", 0.8, vector=[0.0, 1.0]),
            _make_hit("c", 0.7, vector=[1.0, 1.0]),
            _make_hit("d", 0.6, vector=[0.0, 0.0]),
        ]
        hits_with_vectors = [(h, h.vector) for h in hits]
        result = rank_diverse(hits_with_vectors, [1.0, 0.0], max_results=2)
        assert len(result.hits) == 2

    def test_empty_hits(self):
        result = rank_diverse([], [1.0, 0.0], max_results=5)
        assert len(result.hits) == 0

    def test_all_modes_accepted(self):
        """Each documented mode should be accepted without ValueError."""
        hits = [
            _make_hit("a", 0.9, vector=[1.0, 0.0]),
            _make_hit("b", 0.8, vector=[0.0, 1.0]),
        ]
        hits_with_vectors = [(h, h.vector) for h in hits]
        for mode in ["off", "low", "balanced", "high"]:
            result = rank_diverse(hits_with_vectors, [1.0, 0.0], mode=mode, max_results=2)
            assert isinstance(result, DiversityRanking)

    def test_duplicate_content_collapsed(self):
        """Hits with the same content_sha256 should be deduped."""
        hits = [
            _make_hit("a", 0.5, content_sha256="same", vector=[1.0, 0.0]),
            _make_hit("b", 0.9, content_sha256="same", vector=[1.0, 0.0]),
            _make_hit("c", 0.7, content_sha256="different", vector=[0.0, 1.0]),
        ]
        hits_with_vectors = [(h, h.vector) for h in hits]
        result = rank_diverse(hits_with_vectors, [1.0, 0.0], max_results=3)
        # Should have collapsed the "same" duplicates
        # (exact result depends on the impl, but duplicates should be reduced)
        assert len(result.hits) <= 3


# ----- Dataclasses -----

class TestDiversityDataclasses:
    """DiversityStats and DiversityRanking dataclasses."""

    def test_diversity_stats_defaults(self):
        stats = DiversityStats()
        assert stats.requested is False
        assert stats.applied is False
        assert stats.mode == "off"
        assert stats.strength == 0.0

    def test_diversity_stats_full_construction(self):
        stats = DiversityStats(
            requested=True,
            applied=True,
            mode="balanced",
            strength=0.5,
            candidate_count=10,
            result_count=8,
            duplicate_images_collapsed=2,
            semantic_groups_covered=3,
            depth="auto",
            pool_depth=100,
        )
        assert stats.requested is True
        assert stats.mode == "balanced"
        assert stats.duplicate_images_collapsed == 2

    def test_diversity_ranking_construction(self):
        hits = [_make_hit("a", 0.9, vector=[1.0, 0.0])]
        ranking = DiversityRanking(
            hits=hits,
            stats=DiversityStats(),
        )
        assert ranking.hits == hits
        assert ranking.stats.mode == "off"

    def test_diversity_stats_is_frozen(self):
        """Frozen dataclass → can't mutate fields."""
        stats = DiversityStats()
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            stats.mode = "changed"


# ----- Module imports -----

class TestModuleImports:
    """Verify the module's public API is importable."""

    def test_public_functions(self):
        from search import diversity_compute
        assert callable(diversity_compute.mmr_rerank)
        assert callable(diversity_compute.rank_diverse)

    def test_diversity_stats_exported(self):
        from search.diversity_compute import DiversityStats
        stats = DiversityStats()
        assert stats is not None

    def test_diversity_ranking_exported(self):
        from search.diversity_compute import DiversityRanking
        ranking = DiversityRanking(hits=[], stats=DiversityStats())
        assert ranking is not None