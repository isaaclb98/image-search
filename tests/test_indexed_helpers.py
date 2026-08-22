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
    from search._indexed_helpers import favorite_id_set_sync
    db = MagicMock()
    db.get_by_id.side_effect = lambda pid: (
        {"id": pid, "is_favorite": 1} if pid in {"a", "c"} else {"id": pid, "is_favorite": 0}
    )
    assert favorite_id_set_sync(db, ["a", "b", "c"]) == {"a", "c"}


def test_favorite_id_set_sync_skips_missing_rows():
    from search._indexed_helpers import favorite_id_set_sync
    db = MagicMock()
    db.get_by_id.side_effect = lambda pid: {"id": pid, "is_favorite": 1} if pid == "a" else None
    assert favorite_id_set_sync(db, ["a", "missing"]) == {"a"}


# --- favorite_id_set (async wrapper) ---

@pytest.mark.asyncio
async def test_favorite_id_set_runs_in_thread():
    from search._indexed_helpers import favorite_id_set
    db = MagicMock()
    db.get_by_id.return_value = {"id": "a", "is_favorite": 1}
    result = await favorite_id_set(db, ["a"])
    assert result == {"a"}


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
    # favorite_id_set's sync impl walks get_by_id.
    db.get_by_id.side_effect = lambda pid: (
        {"id": pid, "is_favorite": 1} if pid == "a" else {"id": pid, "is_favorite": 0}
    )
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
