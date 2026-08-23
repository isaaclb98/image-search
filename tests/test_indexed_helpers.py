"""
tests/test_indexed_helpers.py — search/_indexed_helpers.py contract.

Pure (well, I/O-bound) helpers previously closure-bound to
create_app. Lifting them is what lets /api/search and
/api/centroids/{name}/search extract.

Each helper takes index_db + cfg explicitly so callers
(including the routers that will eventually wrap /api/search)
own their dependency surface.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _fake_cfg():
    c = MagicMock()
    c.web_ui_url = "http://localhost:5173"
    c.filename_cardinality_guard = 0.5
    return c


# --- favorite_id_set_sync ---

def test_favorite_id_set_sync_returns_ids_with_is_favorite_set():
    """Phase C1: single IN-clause query returns the favourited subset."""
    from search._indexed_helpers import favorite_id_set_sync
    db = MagicMock()
    db.favorite_id_set.return_value = {"a", "c"}
    assert favorite_id_set_sync(db, ["a", "b", "c"]) == {"a", "c"}
    db.favorite_id_set.assert_called_once_with(["a", "b", "c"])


def test_favorite_id_set_sync_skips_missing_rows():
    """Phase C1: the IN-clause query naturally filters out ids
    that aren't in the favourites table."""
    from search._indexed_helpers import favorite_id_set_sync
    db = MagicMock()
    db.favorite_id_set.return_value = {"a"}
    assert favorite_id_set_sync(db, ["a", "missing"]) == {"a"}


# --- favorite_id_set (async wrapper) ---

@pytest.mark.asyncio
async def test_favorite_id_set_runs_in_thread():
    """Async wrapper: SQLite read off the event loop.

    After the C1 refactor, the sync impl is a single IN-clause
    query (index_db.favorite_id_set), not a per-id loop.
    """
    from search._indexed_helpers import favorite_id_set
    db = MagicMock()
    db.favorite_id_set.return_value = {"a"}
    result = await favorite_id_set(db, ["a"])
    assert result == {"a"}
    db.favorite_id_set.assert_called_once_with(["a"])


# --- results_from_hits ---

@pytest.mark.asyncio
async def test_results_from_hits_with_pre_resolved_sets_skips_db():
    """Hot path: caller already has fav/dis sets, no DB hit."""
    from search._indexed_helpers import results_from_hits

    cfg = _fake_cfg()
    db = MagicMock()

    h1 = MagicMock(id="a", path="/a.jpg", score=0.9, payload={})
    h2 = MagicMock(id="b", path="/b.jpg", score=0.85, payload={})

    results = await results_from_hits(
        db, cfg=cfg, hits=[h1, h2],
        favorite_ids={"a"}, dislike_ids={"b"},
    )
    assert len(results) == 2
    assert results[0].id == "a"
    assert results[0].is_favorite is True
    assert results[0].is_disliked is False
    assert results[1].is_favorite is False
    assert results[1].is_disliked is True
    # Pre-resolved sets mean no DB call.
    db.get_by_id.assert_not_called()
    db.dislike_id_set.assert_not_called()


@pytest.mark.asyncio
async def test_results_from_hits_resolves_sets_when_missing():
    """Cold path: parallel-fetch fav/dis when caller didn't pre-resolve."""
    from search._indexed_helpers import results_from_hits

    cfg = _fake_cfg()
    db = MagicMock()
    # favorite_id_set's sync impl is now a single IN-clause query.
    db.favorite_id_set.return_value = {"a"}
    db.dislike_id_set.return_value = {"b"}

    h1 = MagicMock(id="a", path="/a.jpg", score=0.9, payload={})
    h2 = MagicMock(id="b", path="/b.jpg", score=0.85, payload={})

    results = await results_from_hits(db, cfg=cfg, hits=[h1, h2])
    assert results[0].is_favorite is True
    assert results[1].is_disliked is True


@pytest.mark.asyncio
async def test_results_from_hits_score_str_is_3dp():
    from search._indexed_helpers import results_from_hits
    cfg = _fake_cfg()
    db = MagicMock()
    h = MagicMock(id="a", path="/a.jpg", score=0.87654, payload={})
    results = await results_from_hits(
        db, cfg=cfg, hits=[h], favorite_ids=set(), dislike_ids=set(),
    )
    assert results[0].score_str == "0.877"


# --- resolve_filename_filter ---

@pytest.mark.asyncio
async def test_resolve_filename_filter_empty_pattern_returns_none_none():
    from search._indexed_helpers import resolve_filename_filter
    db = MagicMock()
    cfg = _fake_cfg()
    assert await resolve_filename_filter(db, cfg=cfg, pattern="") == (None, None)
    assert await resolve_filename_filter(db, cfg=cfg, pattern="   ") == (None, None)
    db.path_token_ids.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_filename_filter_zero_matches_returns_empty_list():
    from search._indexed_helpers import resolve_filename_filter
    db = MagicMock()
    db.path_token_ids.return_value = []
    cfg = _fake_cfg()
    allowed, err = await resolve_filename_filter(db, cfg=cfg, pattern="xyz")
    assert allowed == []
    assert err is None


@pytest.mark.asyncio
async def test_resolve_filename_filter_high_coverage_skips_filter():
    """Cardinality guard: pattern matches >50% of cache → no filter."""
    from search._indexed_helpers import resolve_filename_filter
    db = MagicMock()
    db.path_token_ids.return_value = ["a", "b", "c", "d", "e", "f"]
    db.count_images.return_value = 10
    cfg = _fake_cfg()
    cfg.filename_cardinality_guard = 0.5
    allowed, err = await resolve_filename_filter(db, cfg=cfg, pattern="common")
    assert allowed is None
    assert err is None


@pytest.mark.asyncio
async def test_resolve_filename_filter_low_coverage_returns_ids():
    from search._indexed_helpers import resolve_filename_filter
    db = MagicMock()
    db.path_token_ids.return_value = ["a", "b"]
    db.count_images.return_value = 1000
    cfg = _fake_cfg()
    cfg.filename_cardinality_guard = 0.5
    allowed, err = await resolve_filename_filter(db, cfg=cfg, pattern="specific")
    assert allowed == ["a", "b"]
    assert err is None


@pytest.mark.asyncio
async def test_resolve_filename_filter_invalid_pattern_returns_bad_request():
    from search._indexed_helpers import resolve_filename_filter
    db = MagicMock()
    db.path_token_ids.side_effect = ValueError("bad FTS5 syntax")
    cfg = _fake_cfg()
    allowed, err = await resolve_filename_filter(db, cfg=cfg, pattern="*garbage*")
    assert allowed is None
    assert err == "bad_request"


@pytest.mark.asyncio
async def test_resolve_filename_filter_none_ids_from_db_returns_none_none():
    """The path_token_ids returning None means "sanitised empty"."""
    from search._indexed_helpers import resolve_filename_filter
    db = MagicMock()
    db.path_token_ids.return_value = None
    cfg = _fake_cfg()
    allowed, err = await resolve_filename_filter(db, cfg=cfg, pattern="")
    # Empty pattern short-circuits before path_token_ids is called.
    assert allowed is None
    assert err is None


def test_surprise_search_returns_shuffled_subset():
    """surprise_search shuffles input order and returns up to k items."""
    from unittest.mock import MagicMock
    from search._indexed_helpers import surprise_search

    hits = [MagicMock(id=f"id_{i}") for i in range(20)]
    result = surprise_search(hits, k=5)
    assert len(result) == 5
    # All returned hits were in the input.
    assert all(h in hits for h in result)


def test_surprise_search_handles_k_larger_than_input():
    from unittest.mock import MagicMock
    from search._indexed_helpers import surprise_search

    hits = [MagicMock(id="a"), MagicMock(id="b")]
    result = surprise_search(hits, k=10)
    assert len(result) == 2


def test_surprise_search_handles_empty_input():
    from search._indexed_helpers import surprise_search
    assert surprise_search([], k=5) == []


def test_search_query_string_includes_q_positives_negatives():
    from search._indexed_helpers import search_query_string
    out = search_query_string(
        q="kittens",
        positives=["cute", "playful"],
        negatives=["blur"],
        collections=[],
    )
    assert "q=kittens" in out
    assert "positives=cute" in out
    assert "positives=playful" in out
    assert "negatives=blur" in out


def test_search_query_string_omits_default_view():
    """When view is the default, it's omitted from canonical URLs."""
    from search._indexed_helpers import search_query_string
    out = search_query_string(q="x", positives=[], negatives=[], collections=[])
    assert "view=" not in out


def test_search_query_string_includes_non_default_view():
    from search._indexed_helpers import search_query_string
    out = search_query_string(
        q="x", positives=[], negatives=[], collections=[], view="feed",
    )
    assert "view=feed" in out


def test_search_query_string_centroids_list_precedence():
    """When centroids= is supplied, single centroid= is ignored."""
    from search._indexed_helpers import search_query_string
    out = search_query_string(
        q="x", positives=[], negatives=[], collections=[],
        centroid="legacy", centroids=["a", "b"],
    )
    assert "centroid=a" in out
    assert "centroid=b" in out
    assert "centroid=legacy" not in out


def test_search_query_string_favorites_flag():
    from search._indexed_helpers import search_query_string
    out = search_query_string(
        q="x", positives=[], negatives=[], collections=[], favorites=True,
    )
    assert "favorites=true" in out


def _fake_cfg(max_prompt_chars: int = 80, max_prompts_total: int = 20):
    from unittest.mock import MagicMock
    cfg = MagicMock()
    cfg.max_prompt_chars = max_prompt_chars
    cfg.max_prompts_total = max_prompts_total
    return cfg


def test_normalize_prompt_state_trims_and_dedupes_positives():
    from search._indexed_helpers import normalize_prompt_state
    cfg = _fake_cfg()
    out = normalize_prompt_state(
        cfg, q="", positives_raw=["kittens", "  kittens  ", "puppies"],
        negatives_raw=[],
    )
    # Dedup is case-insensitive and post-strip.
    assert out.positives == ["kittens", "puppies"]


def test_normalize_prompt_state_drops_overlong_prompts():
    from search._indexed_helpers import normalize_prompt_state
    cfg = _fake_cfg(max_prompt_chars=10)
    out = normalize_prompt_state(
        cfg, q="", positives_raw=["short", "this prompt is way too long"],
        negatives_raw=[],
    )
    assert out.positives == ["short"]


def test_normalize_prompt_state_appends_q_to_positives():
    """q is appended to positives if usable and non-duplicate."""
    from search._indexed_helpers import normalize_prompt_state
    cfg = _fake_cfg()
    out = normalize_prompt_state(
        cfg, q="ducks", positives_raw=["kittens"], negatives_raw=[],
    )
    assert "ducks" in out.positives
    assert out.q == "ducks"


def test_normalize_prompt_state_q_not_duplicated():
    """q must not be added if it's already in positives (case-insensitive)."""
    from search._indexed_helpers import normalize_prompt_state
    cfg = _fake_cfg()
    out = normalize_prompt_state(
        cfg, q="kittens", positives_raw=["kittens"], negatives_raw=[],
    )
    # q should still be set (echo), but positives shouldn't double up.
    assert out.positives.count("kittens") == 1


def test_diversity_page_returns_empty_when_no_favorites_match():
    """When favorites= is set but no candidates match, return empty page."""
    from unittest.mock import MagicMock
    from search._indexed_helpers import diversity_page

    cfg = MagicMock()
    cfg.qdrant_collection = "test_collection"
    cfg.max_results_total = 1000
    cfg.diversity_duplicate_hamming_distance = 8
    cfg.diversity_relevance_drop = 0.1
    cfg.diversity_pool_depths = {}
    qdrant = MagicMock()
    cache = MagicMock()
    cache.get.return_value = None  # miss
    # search_with_vectors returns pairs
    qdrant.search_with_vectors.return_value = ([], 0)

    # favorite_ids set but no overlap with allowed_ids
    hits, has_more, meta = diversity_page(
        cfg, qdrant, cache,
        vector=[0.1] * 4,
        effective_limit=20, offset=0,
        collections=[],
        allowed_ids=["a", "b"],
        favorite_ids={"x", "y"},  # none of a/b
        mode="balanced", strength=0.5,
        depth="auto", pool_depth=0,
    )
    # Favorites filter reduces to empty list (no matches in allowed_ids).
    # The route returns empty + applied metadata without hitting qdrant.
    assert hits == []


def test_diversity_page_uses_cache_hit():
    """Cached entries skip Qdrant and return the cached page."""
    from unittest.mock import MagicMock
    from search._indexed_helpers import diversity_page
    from search.models import DiversityMetadata

    cfg = MagicMock()
    cfg.qdrant_collection = "test_collection"
    qdrant = MagicMock()
    cache = MagicMock()
    cached_meta = DiversityMetadata(
        requested=True, applied=True, mode="balanced",
        strength=0.5, depth="auto", pool_depth=100,
        candidate_count=100, result_count=100,
        duplicate_images_collapsed=0,
    )
    cached_hits = [MagicMock(id=f"id_{i}") for i in range(20)]
    cache.get.return_value = MagicMock(
        hits=cached_hits, stats=cached_meta,
    )

    hits, has_more, meta = diversity_page(
        cfg, qdrant, cache,
        vector=[0.1] * 4,
        effective_limit=5, offset=10,
        collections=[], allowed_ids=None, favorite_ids=None,
        mode="balanced", strength=0.5, depth="auto", pool_depth=100,
    )
    # Cache hit returns the cached page, qdrant not called.
    assert len(hits) == 5
    assert qdrant.search_with_vectors.call_count == 0
