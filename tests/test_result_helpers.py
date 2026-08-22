"""
tests/test_result_helpers.py — search/_result_helpers.py contract.

Pure helper functions that previously lived as closures in
app.py. Lifting them to module level is what lets
/api/search and /api/centroids/{name}/search extract from
the create_app closure (the next refactor pass).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import Request


def test_parse_collections_returns_only_non_empty_in_url_order():
    """getlist() preserves URL order; empty values are filtered."""
    from search._result_helpers import parse_collections

    request = MagicMock(spec=Request)
    request.query_params.getlist.return_value = ["kpop", "", "landscapes", "kpop"]
    assert parse_collections(request) == ["kpop", "landscapes", "kpop"]
    request.query_params.getlist.assert_called_once_with("collection")


def test_coerce_view_passes_through_known_values():
    from search._result_helpers import coerce_view
    assert coerce_view("grid") == "grid"
    assert coerce_view("feed") == "feed"


def test_coerce_view_defaults_to_grid_for_unknown():
    from search._result_helpers import coerce_view
    assert coerce_view(None) == "grid"
    assert coerce_view("") == "grid"
    assert coerce_view("mosaic") == "grid"


def test_diversity_metadata_round_trip():
    from search._result_helpers import diversity_metadata
    from search.diversity import DiversityStats

    stats = DiversityStats(
        requested=True, applied=True, mode="balanced", strength=0.5,
        candidate_count=200, result_count=35,
        duplicate_images_collapsed=4, semantic_groups_covered=8,
        depth="auto", pool_depth=500,
    )
    md = diversity_metadata(stats)
    assert md.requested is True
    assert md.applied is True
    assert md.mode == "balanced"
    assert md.strength == 0.5
    assert md.candidate_count == 200
    assert md.result_count == 35
    assert md.duplicate_images_collapsed == 4
    assert md.semantic_groups_covered == 8
    assert md.depth == "auto"
    assert md.pool_depth == 500


def test_bad_request_returns_400_with_documented_envelope():
    import json

    from search._result_helpers import bad_request
    resp = bad_request("limit must be in [1, 1000]")
    assert resp.status_code == 400
    body = json.loads(bytes(resp.body).decode("utf-8"))
    assert body["error"] == "bad_request"
    assert body["detail"] == "limit must be in [1, 1000]"
    assert body["code"] == "bad_request"


def test_internal_error_returns_500_with_documented_envelope():
    import json

    from search._result_helpers import internal_error
    resp = internal_error("db wedged")
    assert resp.status_code == 500
    body = json.loads(bytes(resp.body).decode("utf-8"))
    assert body["error"] == "internal_error"
    assert body["code"] == "internal_error"


def test_qdrant_unreachable_returns_502_with_documented_envelope():
    import json

    from search._result_helpers import qdrant_unreachable
    resp = qdrant_unreachable("connection refused")
    assert resp.status_code == 502
    body = json.loads(bytes(resp.body).decode("utf-8"))
    assert body["error"] == "qdrant_unreachable"
    assert body["code"] == "qdrant_unreachable"


def test_parse_centroids_strips_and_filters_empty():
    """parse_centroids drops empty/whitespace values, preserves order."""
    from starlette.requests import Request as StarletteRequest
    from search._result_helpers import parse_centroids

    # starlette QueryParams accepts a list of tuples.
    scope = {
        "type": "http",
        "query_string": b"centroid=a&centroid=&centroid=b&centroid=%20",
    }
    request = StarletteRequest(scope)
    assert parse_centroids(request) == ["a", "b"]


def test_parse_centroids_does_not_dedupe():
    """parse_centroids preserves repeated names (multi-weight blend)."""
    from starlette.requests import Request as StarletteRequest
    from search._result_helpers import parse_centroids

    scope = {
        "type": "http",
        "query_string": b"centroid=a&centroid=a&centroid=b",
    }
    request = StarletteRequest(scope)
    assert parse_centroids(request) == ["a", "a", "b"]


def test_parse_weights_broadcasts_single_value():
    """A single weight broadcasts to all n centroids."""
    from starlette.requests import Request as StarletteRequest
    from search._result_helpers import parse_weights

    scope = {"type": "http", "query_string": b"weights=2.5"}
    request = StarletteRequest(scope)
    assert parse_weights(request, n=3) == [2.5, 2.5, 2.5]


def test_parse_weights_comma_separated():
    """Comma-separated form is preferred."""
    from starlette.requests import Request as StarletteRequest
    from search._result_helpers import parse_weights

    scope = {"type": "http", "query_string": b"weights=1,2,3"}
    request = StarletteRequest(scope)
    assert parse_weights(request, n=3) == [1.0, 2.0, 3.0]


def test_parse_weights_returns_none_when_omitted():
    """No weights= param → None (use defaults)."""
    from starlette.requests import Request as StarletteRequest
    from search._result_helpers import parse_weights

    scope = {"type": "http", "query_string": b""}
    request = StarletteRequest(scope)
    assert parse_weights(request, n=3) is None
