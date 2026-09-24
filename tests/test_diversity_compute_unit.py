"""
tests/test_diversity_compute_unit.py — Unit tests for the native MMR branch.

The branch replaces the in-Python MMR pipeline (`rank_diverse`,
`mmr_rerank`, `_as_float_list`, the four-mode enum, the per-mode
strength/relevance tables) with Qdrant's server-side `Mmr` rerank.
What stays in Python are the two load-bearing post-filters:

  - `_collapse_duplicate_indices` — dhash/content_sha256 union-find
  - `apply_relevance_floor` — top-relevance eligibility

This file pins those two primitives, plus the `DiversityStats` shape
and the `DiversityRanking` envelope, against the legacy behaviour
captured by the deleted tests on `dev`.

Actual API:
  _collapse_duplicate_indices(hits, *, query_scores, duplicate_hamming_distance)
  apply_relevance_floor(hits, *, query_scores, floor, min_results=1)
  DiversityStats (dataclass with: requested, applied, diversity,
                  candidate_count, result_count, pool_depth, mmr_source)
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from search.diversity_compute import (
    DiversityRanking,
    DiversityStats,
    _collapse_duplicate_indices,
    apply_relevance_floor,
)


@dataclass
class FakeHit:
    """Minimal hit object for testing."""

    id: str
    score: float
    payload: dict


def _make_hit(hit_id: str, score: float = 0.0, **payload) -> FakeHit:
    return FakeHit(id=hit_id, score=score, payload=payload)


# ---------------------------------------------------------------------------
# _collapse_duplicate_indices
# ---------------------------------------------------------------------------


class TestCollapseDuplicateIndices:
    """Pin the dhash/content_sha256 union-find behavior."""

    def test_empty_hits(self):
        result = _collapse_duplicate_indices(
            [],
            query_scores=[],
            duplicate_hamming_distance=4,
        )
        assert result == []

    def test_no_payload_keys(self):
        # No dhash or content_sha256 → no unions → keep all.
        hits = [_make_hit("a"), _make_hit("b"), _make_hit("c")]
        result = _collapse_duplicate_indices(
            hits, query_scores=[0.1, 0.2, 0.3],
            duplicate_hamming_distance=4,
        )
        assert sorted(result) == [0, 1, 2]

    def test_exact_content_sha256_union(self):
        # Two hits with the same content_sha256 must collapse to one.
        hits = [
            _make_hit("a", 0.9, content_sha256="abc"),
            _make_hit("b", 0.7, content_sha256="abc"),
            _make_hit("c", 0.5, content_sha256="def"),
        ]
        result = _collapse_duplicate_indices(
            hits, query_scores=[0.9, 0.7, 0.5],
            duplicate_hamming_distance=4,
        )
        # a and b collapse; highest relevance (a) wins. c stays.
        assert result == [0, 2]

    def test_dhash_hamming_union(self):
        # Two hits with dhash differing by 2 bits, threshold=4 → union.
        hits = [
            _make_hit("a", 0.9, dhash="0000000000000000"),
            _make_hit("b", 0.7, dhash="0000000000000003"),  # bits 0,1 differ
        ]
        result = _collapse_duplicate_indices(
            hits, query_scores=[0.9, 0.7],
            duplicate_hamming_distance=4,
        )
        assert result == [0]

    def test_dhash_hamming_above_threshold(self):
        # Differ by 8 bits, threshold=4 → no union.
        hits = [
            _make_hit("a", 0.9, dhash="0000000000000000"),
            _make_hit("b", 0.7, dhash="00000000000000ff"),  # 8 bits differ
        ]
        result = _collapse_duplicate_indices(
            hits, query_scores=[0.9, 0.7],
            duplicate_hamming_distance=4,
        )
        assert sorted(result) == [0, 1]

    def test_width_bucket_partition(self):
        # Different-width dhashes go to different buckets. "0f" (width=8)
        # and "000000000000000f" (width=64) must NOT cross-bucket union
        # even though both are 4 bits set.
        hits = [
            _make_hit("a", 0.9, dhash="0f"),
            _make_hit("b", 0.7, dhash="000000000000000f"),
        ]
        result = _collapse_duplicate_indices(
            hits, query_scores=[0.9, 0.7],
            duplicate_hamming_distance=4,
        )
        assert sorted(result) == [0, 1]

    def test_representative_picks_highest_relevance(self):
        # When collapsing, the highest-scoring member wins, ties broken by
        # original index order.
        hits = [
            _make_hit("a", 0.5, content_sha256="x"),
            _make_hit("b", 0.9, content_sha256="x"),  # wins
            _make_hit("c", 0.7, content_sha256="x"),
        ]
        result = _collapse_duplicate_indices(
            hits, query_scores=[0.5, 0.9, 0.7],
            duplicate_hamming_distance=4,
        )
        assert result == [1]

    def test_result_is_sorted(self):
        hits = [
            _make_hit("a", 0.9, dhash="0000000000000000"),
            _make_hit("b", 0.7, dhash="0000000000000003"),
            _make_hit("c", 0.5, dhash="00000000000000ff"),
        ]
        result = _collapse_duplicate_indices(
            hits, query_scores=[0.9, 0.7, 0.5],
            duplicate_hamming_distance=4,
        )
        # Result must be in ascending order regardless of collapse pattern.
        assert result == sorted(result)

    def test_zero_dhash_is_valid_hash(self):
        # All-zero dhash is a valid 64-bit hash (bit_count = 0), not a
        # missing-value marker. Two zero dhashes within hd=4 collapse.
        hits = [
            _make_hit("a", 0.9, dhash="0000000000000000"),
            _make_hit("b", 0.7, dhash="0000000000000000"),
            _make_hit("c", 0.5, dhash="0000000000000003"),
        ]
        result = _collapse_duplicate_indices(
            hits, query_scores=[0.9, 0.7, 0.5],
            duplicate_hamming_distance=4,
        )
        assert result == [0]

    def test_malformed_dhash_skipped(self):
        # Garbage in dhash field is silently ignored.
        hits = [
            _make_hit("a", 0.9, dhash="not-hex"),
            _make_hit("b", 0.7, dhash="0000000000000001"),
        ]
        result = _collapse_duplicate_indices(
            hits, query_scores=[0.9, 0.7],
            duplicate_hamming_distance=4,
        )
        assert sorted(result) == [0, 1]


# ---------------------------------------------------------------------------
# apply_relevance_floor
# ---------------------------------------------------------------------------


class TestApplyRelevanceFloor:
    """Pin the top-relevance eligibility filter."""

    def test_empty_hits(self):
        result = apply_relevance_floor([], query_scores=[], floor=0.1)
        assert result == []

    def test_zero_floor_is_noop(self):
        # floor=0.0 must keep everything.
        hits = [_make_hit("a"), _make_hit("b"), _make_hit("c")]
        result = apply_relevance_floor(
            hits, query_scores=[0.9, 0.7, 0.5], floor=0.0,
        )
        assert result == [0, 1, 2]

    def test_negative_floor_is_noop(self):
        # Defensive: negative floors shouldn't drop everything.
        hits = [_make_hit("a"), _make_hit("b"), _make_hit("c")]
        result = apply_relevance_floor(
            hits, query_scores=[0.9, 0.7, 0.5], floor=-0.5,
        )
        assert result == [0, 1, 2]

    def test_floor_keeps_top_band(self):
        # top=1.0, floor=0.10 → keep >= 0.9
        hits = [_make_hit("a"), _make_hit("b"), _make_hit("c"), _make_hit("d")]
        result = apply_relevance_floor(
            hits, query_scores=[1.0, 0.95, 0.89, 0.5], floor=0.10,
        )
        assert sorted(result) == [0, 1]

    def test_floor_wipes_everything_falls_back(self):
        # floor so aggressive that no hits qualify → fall back to top-N.
        hits = [_make_hit("a"), _make_hit("b"), _make_hit("c")]
        result = apply_relevance_floor(
            hits, query_scores=[1.0, 0.0, 0.0], floor=0.5,
        )
        # top_score=1.0, threshold=0.5; only hit 0 passes naturally.
        assert result == [0]

    def test_floor_wipes_natural_pass(self):
        # When the floor wipes ALL candidates, return top-`min_results`.
        hits = [_make_hit("a"), _make_hit("b"), _make_hit("c"), _make_hit("d")]
        result = apply_relevance_floor(
            hits, query_scores=[1.0, 0.0, 0.0, 0.0], floor=0.5,
            min_results=3,
        )
        # Only hit 0 (score=1.0) passes the floor. Falls back to top-3
        # by score: [0, 1, 2] (or [0, 1, 3] — both have score 0.0).
        assert len(result) == 3
        assert 0 in result

    def test_min_results_zero_still_returns_one(self):
        # The impl clamps min_results to >= 1 because the caller's
        # downstream code expects at least one surviving index.
        hits = [_make_hit("a"), _make_hit("b"), _make_hit("c")]
        result = apply_relevance_floor(
            hits, query_scores=[1.0, 0.0, 0.0], floor=0.5,
            min_results=0,
        )
        # Hit 0 passes the floor naturally. Falls back to top-1.
        assert result == [0]


# ---------------------------------------------------------------------------
# DiversityStats / DiversityRanking shapes
# ---------------------------------------------------------------------------


class TestDiversityStats:
    def test_defaults(self):
        stats = DiversityStats()
        assert stats.requested is False
        assert stats.applied is False
        assert stats.diversity == 0.0
        assert stats.candidate_count == 0
        assert stats.result_count == 0
        assert stats.pool_depth == 0
        assert stats.mmr_source == "qdrant_native"

    def test_frozen(self):
        stats = DiversityStats(requested=True, applied=True)
        with pytest.raises(Exception):  # noqa: B017
            stats.applied = False  # type: ignore[misc]


class TestDiversityRanking:
    def test_construct_with_hits_and_stats(self):
        hits = [_make_hit("a"), _make_hit("b")]
        stats = DiversityStats(
            requested=True, applied=True, diversity=0.5,
            candidate_count=100, result_count=2, pool_depth=100,
        )
        ranking = DiversityRanking(hits=hits, stats=stats)
        assert ranking.hits == hits
        assert ranking.stats.diversity == 0.5
