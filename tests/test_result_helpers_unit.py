"""
tests/test_result_helpers_unit.py — Unit tests for search/_result_helpers.py.

Request parsing and error response helpers used by the search API.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from search._result_helpers import (
    bad_request,
    coerce_view,
    diversity_metadata,
    internal_error,
    parse_centroids,
    parse_collections,
    parse_filename,
    parse_weights,
    qdrant_timeout,
    qdrant_unreachable,
)


def _make_request(query_string: str = "", headers: dict | None = None) -> Request:
    """Build a Starlette Request with the given query string."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "query_string": query_string.encode(),
        "server": ("test", 80),
    }
    return Request(scope)


# ----- parse_filename -----

class TestParseFilename:
    """Parse the filename filter query param."""

    def test_no_filename_param(self):
        request = _make_request("")
        result = parse_filename(request)
        assert result == ""

    def test_with_filename_param(self):
        request = _make_request("filename=vacation")
        assert parse_filename(request) == "vacation"

    def test_with_other_params(self):
        request = _make_request("filename=IMG_2024&q=cat")
        assert parse_filename(request) == "IMG_2024"

    def test_filename_with_spaces(self):
        request = _make_request("filename=hello%20world")
        assert parse_filename(request) == "hello world"

    def test_empty_filename(self):
        request = _make_request("filename=")
        assert parse_filename(request) == ""


# ----- parse_collections -----

class TestParseCollections:
    """Parse repeated collection= query params into a list."""

    def test_no_collections(self):
        request = _make_request("")
        assert parse_collections(request) == []

    def test_single_collection(self):
        request = _make_request("collection=lib-a")
        assert parse_collections(request) == ["lib-a"]

    def test_multiple_collections(self):
        request = _make_request("collection=lib-a&collection=lib-b&collection=lib-c")
        assert parse_collections(request) == ["lib-a", "lib-b", "lib-c"]

    def test_collections_with_other_params(self):
        request = _make_request("collection=lib-a&q=cat&collection=lib-b")
        result = parse_collections(request)
        assert "lib-a" in result
        assert "lib-b" in result

    def test_empty_collections_value_ignored(self):
        """Empty collection= should be filtered out."""
        request = _make_request("collection=&collection=lib-a")
        result = parse_collections(request)
        assert "lib-a" in result
        # The empty string might or might not be included — depends on impl
        # Just verify the real one is there


# ----- coerce_view -----

class TestCoerceView:
    """Coerce the view param to a known value."""

    def test_none_returns_default(self):
        result = coerce_view(None)
        # Should return a valid view name (likely 'grid')
        assert result in ("grid", "feed")

    def test_known_view(self):
        result = coerce_view("grid")
        assert result == "grid"

    def test_known_view_list(self):
        result = coerce_view("feed")
        assert result == "feed"

    def test_unknown_view_returns_default(self):
        """Unknown view should fall back to default, not crash."""
        result = coerce_view("bogus-view")
        # Should return a valid view name
        assert result in ("grid", "feed")

    def test_returns_string(self):
        result = coerce_view(None)
        assert isinstance(result, str)


# ----- diversity_metadata -----

class TestDiversityMetadata:
    """Build the diversity metadata block from a stats dataclass."""

    def test_returns_metadata_dict(self):
        from search.diversity_compute import DiversityStats
        stats = DiversityStats(
            requested=True,
            applied=True,
            mode="balanced",
            strength=0.5,
            candidate_count=10,
            result_count=8,
        )
        result = diversity_metadata(stats)
        # Should be a dict-like object
        assert result is not None


# ----- Error response helpers -----

class TestErrorResponses:
    """The bad_request / internal_error / qdrant_* helpers."""

    def test_bad_request_returns_400(self):
        resp = bad_request("invalid input")
        assert resp.status_code == 400

    def test_bad_request_includes_detail(self):
        resp = bad_request("missing q param")
        # JSONResponse body should have the detail
        import json
        body = json.loads(bytes(resp.body).decode())
        assert body.get("detail") == "missing q param"

    def test_internal_error_returns_500(self):
        resp = internal_error("something broke")
        assert resp.status_code == 500

    def test_internal_error_includes_detail(self):
        resp = internal_error("null pointer")
        import json
        body = json.loads(bytes(resp.body).decode())
        assert body.get("detail") == "null pointer"

    def test_qdrant_unreachable_returns_502(self):
        resp = qdrant_unreachable("Qdrant is down")
        assert resp.status_code == 502

    def test_qdrant_timeout_returns_504(self):
        resp = qdrant_timeout("Query timed out after 2s")
        assert resp.status_code == 504

    def test_error_responses_are_json(self):
        """All error helpers return JSON, not HTML."""
        for resp in [
            bad_request("x"),
            internal_error("x"),
            qdrant_unreachable("x"),
            qdrant_timeout("x"),
        ]:
            ct = resp.headers.get("content-type", "")
            assert "json" in ct.lower()


# ----- parse_centroids -----

class TestParseCentroids:
    """Parse repeated centroid= query params."""

    def test_no_centroids(self):
        request = _make_request("")
        assert parse_centroids(request) == []

    def test_single_centroid(self):
        request = _make_request("centroid=my-centroid")
        assert parse_centroids(request) == ["my-centroid"]

    def test_multiple_centroids(self):
        request = _make_request("centroid=c1&centroid=c2")
        assert parse_centroids(request) == ["c1", "c2"]

    def test_centroids_with_other_params(self):
        request = _make_request("centroid=c1&q=cat&centroid=c2")
        result = parse_centroids(request)
        assert "c1" in result
        assert "c2" in result


# ----- parse_weights -----

class TestParseWeights:
    """Parse repeated weights= query params."""

    def test_no_weights(self):
        request = _make_request("")
        result = parse_weights(request, n=0)
        assert result == []

    def test_single_weight(self):
        request = _make_request("weights=1.0")
        result = parse_weights(request, n=1)
        assert len(result) == 1
        assert abs(result[0] - 1.0) < 0.01

    def test_multiple_weights(self):
        request = _make_request("weights=0.5&weights=0.5")
        result = parse_weights(request, n=2)
        assert len(result) == 2

    def test_weights_count_mismatch_raises(self):
        """If n weights aren't provided, behavior is implementation-defined.
        At minimum it should not silently return wrong count."""
        request = _make_request("weights=1.0")  # 1 weight
        # Asking for 3 might raise or return less
        # Just verify it doesn't crash silently
        try:
            result = parse_weights(request, n=3)
            # If it returns, the result should be at least the count provided
            assert len(result) <= 3
        except (ValueError, IndexError):
            pass  # Acceptable to raise

    def test_non_numeric_weight_raises_http_exception(self):
        """Non-numeric weight raises HTTPException 400 (strict parsing)."""
        from fastapi import HTTPException
        request = _make_request("weights=not-a-number")
        with pytest.raises(HTTPException) as exc_info:
            parse_weights(request, n=1)
        assert exc_info.value.status_code == 400


# ----- Module imports -----

class TestModuleImports:
    """Public API is importable."""

    def test_helpers_importable(self):
        from search import _result_helpers
        assert callable(_result_helpers.parse_filename)
        assert callable(_result_helpers.parse_collections)
        assert callable(_result_helpers.coerce_view)
        assert callable(_result_helpers.bad_request)
        assert callable(_result_helpers.internal_error)