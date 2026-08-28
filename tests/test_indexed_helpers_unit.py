"""
tests/test_indexed_helpers_unit.py — Unit tests for search/_indexed_helpers.py.

Pure-function helpers used by /api/search. No database or network
needed — they take state as parameters and return shaped results.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from search._indexed_helpers import (
    _digest_values,
    favorite_id_set_sync,
    surprise_search,
)
from search.qdrant_client import SearchHit


# ----- favorite_id_set_sync -----

class TestFavoriteIdSetSync:
    """Sync wrapper that reads favourite bit for each point_id."""

    def test_delegates_to_index_db(self):
        mock_db = MagicMock()
        mock_db.favorite_id_set.return_value = {"id1", "id3"}
        result = favorite_id_set_sync(mock_db, ["id1", "id2", "id3"])
        assert result == {"id1", "id3"}
        mock_db.favorite_id_set.assert_called_once_with(["id1", "id2", "id3"])

    def test_empty_input_returns_empty_set(self):
        mock_db = MagicMock()
        mock_db.favorite_id_set.return_value = set()
        result = favorite_id_set_sync(mock_db, [])
        assert result == set()

    def test_no_favorites_returns_empty_set(self):
        mock_db = MagicMock()
        mock_db.favorite_id_set.return_value = set()
        result = favorite_id_set_sync(mock_db, ["unknown-id"])
        assert result == set()


def _make_hit(id: str, score: float = 0.5) -> SearchHit:
    return SearchHit(id=id, path=f"/img/{id}.jpg", score=score)


# ----- surprise_search -----

class TestSurpriseSearch:
    """Shuffle hits and return up to k."""

    def test_returns_up_to_k_hits(self):
        hits = [_make_hit(f"id-{i}") for i in range(10)]
        result = surprise_search(hits, k=3)
        assert len(result) == 3

    def test_k_larger_than_hits_returns_all(self):
        hits = [_make_hit(f"id-{i}") for i in range(3)]
        result = surprise_search(hits, k=10)
        assert len(result) == 3

    def test_k_zero_returns_empty(self):
        hits = [_make_hit("id-1")]
        result = surprise_search(hits, k=0)
        assert len(result) == 0

    def test_empty_input_returns_empty(self):
        result = surprise_search([], k=5)
        assert result == []

    def test_does_not_mutate_input(self):
        """surprise_search should shuffle a copy, not the input list."""
        hits = [_make_hit(f"id-{i}") for i in range(10)]
        original_ids = [h.id for h in hits]
        _ = surprise_search(hits, k=10)
        # Input list should still have the original order
        assert [h.id for h in hits] == original_ids

    def test_returns_subset_of_input(self):
        """All returned hits should be from the input list."""
        hits = [_make_hit(f"id-{i}") for i in range(20)]
        result = surprise_search(hits, k=10)
        input_ids = {h.id for h in hits}
        result_ids = {h.id for h in result}
        assert result_ids.issubset(input_ids)

    def test_returns_searchhit_objects(self):
        hits = [_make_hit("id-1"), _make_hit("id-2")]
        result = surprise_search(hits, k=2)
        for hit in result:
            assert isinstance(hit, SearchHit)


# ----- _digest_values -----

class TestDigestValues:
    """Stable hash for cache key generation."""

    def test_same_values_same_hash(self):
        """Identical value lists produce the same digest."""
        assert _digest_values(["a", "b", "c"]) == _digest_values(["a", "b", "c"])

    def test_different_values_different_hash(self):
        assert _digest_values(["a", "b"]) != _digest_values(["a", "c"])

    def test_different_order_same_hash(self):
        """Order doesn't matter — values are sorted before hashing.

        This is by design: equivalent inputs in any order produce the
        same cache key (see docstring).
        """
        assert _digest_values(["a", "b"]) == _digest_values(["b", "a"])

    def test_returns_string(self):
        result = _digest_values(["a", "b"])
        assert isinstance(result, str)

    def test_returns_hex_like_string(self):
        """Digests are typically hex strings."""
        result = _digest_values(["a"])
        # Should be a reasonable hash length
        assert len(result) >= 8

    def test_empty_list_produces_valid_hash(self):
        """An empty list is a valid input."""
        result = _digest_values([])
        assert isinstance(result, str)

    def test_handles_tuples(self):
        """Tuples should be hashable too."""
        result = _digest_values(("a", "b"))
        assert isinstance(result, str)


# ----- search_query_string -----

class TestSearchQueryString:
    """Build multi-value search-state query strings for back-links."""

    def test_simple_query(self):
        from search._indexed_helpers import search_query_string
        qs = search_query_string(
            q="cat",
            positives=[],
            negatives=[],
            collections=[],
        )
        assert "q=cat" in qs

    def test_multi_positives(self):
        from search._indexed_helpers import search_query_string
        qs = search_query_string(
            q="",
            positives=["cat", "dog"],
            negatives=[],
            collections=[],
        )
        assert "positives=cat" in qs
        assert "positives=dog" in qs

    def test_view_excluded_when_default(self):
        """view=grid is default → should be omitted from URL."""
        from search._indexed_helpers import search_query_string
        qs = search_query_string(
            q="cat",
            positives=[],
            negatives=[],
            collections=[],
            view="grid",
        )
        assert "view=grid" not in qs

    def test_view_included_when_non_default(self):
        from search._indexed_helpers import search_query_string
        qs = search_query_string(
            q="cat",
            positives=[],
            negatives=[],
            collections=[],
            view="list",
        )
        assert "view=list" in qs

    def test_filename_filter(self):
        from search._indexed_helpers import search_query_string
        qs = search_query_string(
            q="cat",
            positives=[],
            negatives=[],
            collections=[],
            filename="vacation",
        )
        assert "filename=vacation" in qs

    def test_centroid_single_legacy_param(self):
        from search._indexed_helpers import search_query_string
        qs = search_query_string(
            q="cat",
            positives=[],
            negatives=[],
            collections=[],
            centroid="my-centroid",
        )
        assert "centroid=my-centroid" in qs

    def test_centroids_list(self):
        from search._indexed_helpers import search_query_string
        qs = search_query_string(
            q="cat",
            positives=[],
            negatives=[],
            collections=[],
            centroids=["c1", "c2"],
        )
        assert "centroid=c1" in qs
        assert "centroid=c2" in qs

    def test_returns_string(self):
        from search._indexed_helpers import search_query_string
        qs = search_query_string(
            q="cat",
            positives=[],
            negatives=[],
            collections=[],
        )
        assert isinstance(qs, str)


# ----- Module imports -----

class TestModuleImports:
    """The helper module's public API."""

    def test_helpers_importable(self):
        from search import _indexed_helpers
        assert callable(_indexed_helpers.favorite_id_set_sync)
        assert callable(_indexed_helpers.surprise_search)
        assert callable(_indexed_helpers.search_query_string)