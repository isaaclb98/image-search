"""
tests/test_shuffled_for_you.py — Backend tests for shuffled-for-you.

Three test groups:
1. Pure math (explore_pool_size): smoke + boundary cases.
2. Pool builder (build_pool): recommend() path, cold-start zero-vector
   path, cache hit/miss/TTL/invalidation behaviour.
3. Router (build_shuffled_for_you_router): happy path with mocked
   deps, response shape, param validation, error handling.
"""
from __future__ import annotations

import random
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from search.for_you_compute import explore_pool_size
from search.shuffled_for_you import (
    SHUFFLED_MAX_LIMIT,
    build_pool,
    invalidate_pool_cache,
    rank,
)


# ===========================================================================
# 1. Pure math — explore_pool_size
# ===========================================================================


class TestExplorePoolSize:
    @pytest.mark.parametrize(
        "library_size, top_pct, expected",
        [
            (800_000, 1.0, 8000),
            (100_000, 1.0, 1000),
            (50_000, 5.0, 2500),
            (1000, 100.0, 1000),
            (1, 1.0, 1),
            (100, 0.001, 1),
            (0, 1.0, 1),
        ],
    )
    def test_basic(self, library_size: int, top_pct: float, expected: int) -> None:
        assert explore_pool_size(library_size, top_pct) == expected

    def test_real_prod_case(self) -> None:
        """Smoke test against the actual deployed library size."""
        assert explore_pool_size(800_123, 1.0) == 8001

    def test_no_ceiling(self) -> None:
        """Per Isaac: 'no pool ceiling.' top_pct=5 over a 5M library returns 250k."""
        assert explore_pool_size(5_000_000, 5.0) == 250_000

    @pytest.mark.parametrize(
        "library_size, top_pct",
        [
            (-1, 1.0),
            (100, 0.0),
            (100, -1.0),
            (100, 100.1),
            (100, 200.0),
        ],
    )
    def test_invalid_inputs(self, library_size: int, top_pct: float) -> None:
        with pytest.raises(ValueError, match=r"must be"):
            explore_pool_size(library_size, top_pct)


# ===========================================================================
# 2. Pool builder — build_pool
# ===========================================================================


def _hit(hit_id: str) -> MagicMock:
    """Build a fake SearchHit-like object with proper payload data."""
    h = MagicMock()
    h.id = hit_id
    h.path = f"/nas/path/{hit_id}.jpg"
    h.payload = {
        "path": f"/nas/path/{hit_id}.jpg",
        "blurhash": "L00000L00000",
        "width": 800,
        "height": 600,
    }
    return h


class TestBuildPool:
    def setup_method(self) -> None:
        invalidate_pool_cache()

    def test_uses_recommend_when_favorites_present(self) -> None:
        """Favorites → qdrant.recommend(), not the zero-vector search."""
        qdrant = MagicMock()
        qdrant.recommend.return_value = [_hit("a"), _hit("b"), _hit("c")]
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 300

        rng = random.Random(42)  # noqa: S311
        result = build_pool(
            fav_ids=["fav-1"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            top_pct=1.0,
            rng=rng,
        )
        assert sorted(result) == ["a", "b", "c"]
        qdrant.recommend.assert_called_once()
        call_kwargs = qdrant.recommend.call_args.kwargs
        assert call_kwargs["positive"] == ["fav-1"]
        assert call_kwargs["limit"] == 3
        qdrant.search.assert_not_called()

    def test_uses_zero_vector_search_for_cold_start(self) -> None:
        """No favorites → qdrant.search with zero vector (recommend requires positives)."""
        qdrant = MagicMock()
        qdrant.search.return_value = ([_hit("x"), _hit("y")], None)
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 200

        result = build_pool(
            fav_ids=[],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            top_pct=1.0,
        )
        assert sorted(result) == ["x", "y"]
        qdrant.recommend.assert_not_called()
        qdrant.search.assert_called_once()

    def test_returns_empty_when_library_is_empty(self) -> None:
        """0 points in library → empty pool without calling Qdrant."""
        qdrant = MagicMock()
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 0

        result = build_pool(
            fav_ids=["fav"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            top_pct=1.0,
        )
        assert result == []
        qdrant.recommend.assert_not_called()
        qdrant.search.assert_not_called()

    def test_returns_empty_when_qdrant_count_unreachable(self) -> None:
        """qdrant_point_count returning -1 (unreachable) → empty pool."""
        qdrant = MagicMock()
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = -1

        result = build_pool(
            fav_ids=["fav"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            top_pct=1.0,
        )
        assert result == []
        qdrant.recommend.assert_not_called()

    def test_cache_hit_within_ttl(self) -> None:
        """Second call within TTL reuses the cached pool without hitting Qdrant."""
        qdrant = MagicMock()
        qdrant.recommend.return_value = [_hit("a"), _hit("b")]
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 200

        result_1 = build_pool(
            fav_ids=["fav-1"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            top_pct=1.0,
            rng=random.Random(42),  # noqa: S311
        )
        result_2 = build_pool(
            fav_ids=["fav-1"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            top_pct=1.0,
            rng=random.Random(99),  # noqa: S311  # different rng — should be IGNORED
        )
        assert result_1 == result_2
        qdrant.recommend.assert_called_once()

    def test_cache_miss_after_invalidate(self) -> None:
        """After invalidate_pool_cache(), the next call rebuilds."""
        qdrant = MagicMock()
        qdrant.recommend.side_effect = [
            [_hit("a"), _hit("b")],
            [_hit("c"), _hit("d")],
        ]
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 200

        build_pool(
            fav_ids=["fav-1"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            top_pct=1.0,
        )
        invalidate_pool_cache()
        result_2 = build_pool(
            fav_ids=["fav-1"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            top_pct=1.0,
        )
        assert sorted(result_2) == ["c", "d"]
        assert qdrant.recommend.call_count == 2

    def test_different_top_pct_yields_different_cache_key(self) -> None:
        """top_pct is part of the cache key — different values don't share."""
        qdrant = MagicMock()
        qdrant.recommend.side_effect = [
            [_hit("a")],
            [_hit("b"), _hit("c")],
        ]
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 100

        build_pool(fav_ids=["fav"], dis_ids=[], qdrant=qdrant, index_db=index_db, top_pct=1.0)
        build_pool(fav_ids=["fav"], dis_ids=[], qdrant=qdrant, index_db=index_db, top_pct=2.0)
        assert qdrant.recommend.call_count == 2
        assert qdrant.recommend.call_args_list[0].kwargs["limit"] == 1
        assert qdrant.recommend.call_args_list[1].kwargs["limit"] == 2

    def test_different_fav_ids_yields_different_cache_key(self) -> None:
        """fav_ids is part of the cache key — different user signals don't share."""
        qdrant = MagicMock()
        qdrant.recommend.side_effect = [[_hit("a")], [_hit("b")]]
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 100

        build_pool(fav_ids=["fav-1"], dis_ids=[], qdrant=qdrant, index_db=index_db, top_pct=1.0)
        build_pool(fav_ids=["fav-2"], dis_ids=[], qdrant=qdrant, index_db=index_db, top_pct=1.0)
        assert qdrant.recommend.call_count == 2

    def test_shuffle_covers_all_ids(self) -> None:
        """Sanity: the returned ids cover the full recommend() output."""
        qdrant = MagicMock()
        qdrant.recommend.return_value = [_hit(f"id-{i:03d}") for i in range(20)]
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 2000

        result = build_pool(
            fav_ids=["fav"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            top_pct=1.0,
            rng=random.Random(0),  # noqa: S311
        )
        assert sorted(result) == [f"id-{i:03d}" for i in range(20)]


# ===========================================================================
# 3. rank() — page slicing + retrieve_batch
# ===========================================================================


class TestRank:
    def setup_method(self) -> None:
        invalidate_pool_cache()

    def test_returns_page_sliced_from_pool(self) -> None:
        """page=0, limit=2 returns the first 2 ids from the shuffled pool."""
        qdrant = MagicMock()
        pool = [_hit(f"id-{i}") for i in range(10)]
        qdrant.recommend.return_value = pool
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 1000

        # Mock retrieve_batch to return ALL hits; rank() will
        # re-order by the shuffled pool order.
        qdrant.retrieve_batch.return_value = list(pool)

        hits, pool_total, has_more = rank(
            fav_ids=["fav"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            limit=2,
            page=0,
            top_pct=1.0,
            rng=random.Random(0),  # noqa: S311
        )
        assert pool_total == 10
        assert has_more is True
        # The first 2 hits should be the first 2 of the shuffled pool.
        pool_ids = [h.id for h in pool]
        shuffled = list(pool_ids)
        random.Random(0).shuffle(shuffled)  # noqa: S311
        assert [h.id for h in hits] == shuffled[:2]

    def test_has_more_false_on_last_page(self) -> None:
        """page=1, limit=2 with pool of 4 → 2 results, has_more=False."""
        qdrant = MagicMock()
        qdrant.recommend.return_value = [_hit(f"id-{i}") for i in range(4)]
        qdrant.retrieve_batch.return_value = [_hit("id-a"), _hit("id-b")]
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 400

        _, pool_total, has_more = rank(
            fav_ids=["fav"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            limit=2,
            page=1,
            top_pct=1.0,
            rng=random.Random(0),  # noqa: S311
        )
        assert pool_total == 4
        assert has_more is False

    def test_empty_pool_returns_empty(self) -> None:
        """0 library size → empty hits, total=0, has_more=False."""
        qdrant = MagicMock()
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 0

        hits, pool_total, has_more = rank(
            fav_ids=["fav"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            limit=10,
            page=0,
            top_pct=1.0,
        )
        assert hits == []
        assert pool_total == 0
        assert has_more is False
        qdrant.retrieve_batch.assert_not_called()

    def test_page_past_end_returns_empty_with_correct_total(self) -> None:
        """page=99 with pool of 4 → empty hits but pool_total still correct."""
        qdrant = MagicMock()
        qdrant.recommend.return_value = [_hit(f"id-{i}") for i in range(4)]
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 400

        hits, pool_total, has_more = rank(
            fav_ids=["fav"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            limit=2,
            page=99,
            top_pct=1.0,
            rng=random.Random(0),  # noqa: S311
        )
        assert hits == []
        assert pool_total == 4
        assert has_more is False

    def test_limit_clamped_to_max(self) -> None:
        """limit > SHUFFLED_MAX_LIMIT → silently clamped to SHUFFLED_MAX_LIMIT."""
        qdrant = MagicMock()
        qdrant.recommend.return_value = [_hit(f"id-{i}") for i in range(50)]
        qdrant.retrieve_batch.return_value = [_hit(f"id-{i}") for i in range(50)]
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 5000

        rank(
            fav_ids=["fav"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            limit=SHUFFLED_MAX_LIMIT + 50,
            page=0,
            top_pct=1.0,
            rng=random.Random(0),  # noqa: S311
        )
        called_ids = qdrant.retrieve_batch.call_args.args[0]
        assert len(called_ids) <= SHUFFLED_MAX_LIMIT

    def test_retrieve_batch_in_pool_order(self) -> None:
        """Returned hits are in the shuffled pool order, not arbitrary."""
        qdrant = MagicMock()
        pool = [_hit(f"id-{i}") for i in range(5)]
        qdrant.recommend.return_value = pool
        # retrieve_batch returns hits in a deliberately shuffled order
        c, a, e, b, d = _hit("id-0"), _hit("id-1"), _hit("id-2"), _hit("id-3"), _hit("id-4")
        qdrant.retrieve_batch.return_value = [c, a, e, b, d]
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 500

        hits, _, _ = rank(
            fav_ids=["fav"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            limit=5,
            page=0,
            top_pct=1.0,
            rng=random.Random(0),  # noqa: S311
        )
        pool_ids = [h.id for h in pool]
        shuffled = list(pool_ids)
        random.Random(0).shuffle(shuffled)  # noqa: S311
        expected_order = shuffled[:5]
        actual_order = [h.id for h in hits]
        assert actual_order == expected_order

    def test_missing_ids_filtered_out(self) -> None:
        """retrieve_batch that omits some ids → only present ones returned."""
        qdrant = MagicMock()
        qdrant.recommend.return_value = [_hit(f"id-{i}") for i in range(5)]
        qdrant.retrieve_batch.return_value = [_hit("id-0"), _hit("id-2"), _hit("id-4")]
        index_db = MagicMock()
        index_db.qdrant_point_count.return_value = 500

        hits, _, _ = rank(
            fav_ids=["fav"],
            dis_ids=[],
            qdrant=qdrant,
            index_db=index_db,
            limit=5,
            page=0,
            top_pct=1.0,
            rng=random.Random(0),  # noqa: S311
        )
        returned_ids = [h.id for h in hits]
        for rid in returned_ids:
            assert int(rid.split("-")[1]) % 2 == 0


# ===========================================================================
# 4. Router — build_shuffled_for_you_router
# ===========================================================================


def _fake_index_db(fav_ids=None, dis_ids=None, point_count=100):
    db = MagicMock()
    db.list_favorite_ids.return_value = fav_ids or []
    db.list_dislike_ids.return_value = dis_ids or []
    db.qdrant_point_count.return_value = point_count
    return db


def _fake_qdrant(hit_ids):
    q = MagicMock()
    hits = [_hit(hid) for hid in hit_ids]
    q.recommend.return_value = hits
    q.search.return_value = (hits, None)
    q.retrieve_batch.return_value = hits
    return q


def _fake_cfg(web_ui_url="http://localhost:8000"):
    cfg = MagicMock()
    cfg.web_ui_url = web_ui_url
    return cfg


def _wrap(router):
    """Wrap a router in a minimal FastAPI app for TestClient.

    starlette's TestClient requires middleware to be set up via
    FastAPI; the bare router doesn't. Uses `with TestClient(app)`
    in the test bodies (per the project's existing convention in
    tests/test_routers_random.py).
    """
    app = FastAPI()
    app.include_router(router)
    return app


class TestRouter:
    def setup_method(self) -> None:
        invalidate_pool_cache()

    def _build_router(self, fav_ids=None, point_count=1000, hit_ids=None):
        """Helper: build a wrapped app with sensible defaults."""
        from search.routers.shuffled_for_you import build_shuffled_for_you_router

        index_db = _fake_index_db(fav_ids=fav_ids, point_count=point_count)
        qdrant = _fake_qdrant(hit_ids or [f"id-{i}" for i in range(10)])
        cfg = _fake_cfg()
        router = build_shuffled_for_you_router(
            index_db=index_db, qdrant=qdrant, cfg=cfg,
        )
        return _wrap(router), index_db, qdrant

    def test_happy_path_returns_search_response_shape(self) -> None:
        """Default params: top_pct=1, limit=30, page=0 → SearchResponse with results."""
        app, _, _ = self._build_router(fav_ids=["fav-1"], point_count=1000)
        with TestClient(app) as client:
            response = client.get("/api/shuffled-for-you/feed")
        assert response.status_code == 200
        body = response.json()
        assert "results" in body
        assert "session_total" in body
        assert "has_more" in body
        assert "offset" in body
        assert "limit" in body

    def test_top_pct_zero_rejected(self) -> None:
        """top_pct=0.0 → 422 (Query validator)."""
        app, _, _ = self._build_router()
        with TestClient(app) as client:
            response = client.get("/api/shuffled-for-you/feed?top_pct=0.0")
        assert response.status_code == 422

    def test_top_pct_over_100_rejected(self) -> None:
        """top_pct=101 → 422."""
        app, _, _ = self._build_router()
        with TestClient(app) as client:
            response = client.get("/api/shuffled-for-you/feed?top_pct=101")
        assert response.status_code == 422

    def test_limit_zero_rejected(self) -> None:
        """limit=0 → 422."""
        app, _, _ = self._build_router()
        with TestClient(app) as client:
            response = client.get("/api/shuffled-for-you/feed?limit=0")
        assert response.status_code == 422

    def test_page_negative_rejected(self) -> None:
        """page=-1 → 422."""
        app, _, _ = self._build_router()
        with TestClient(app) as client:
            response = client.get("/api/shuffled-for-you/feed?page=-1")
        assert response.status_code == 422

    def test_response_results_have_required_fields(self) -> None:
        """Each result has id, path, url, blurhash, width, height."""
        app, _, _ = self._build_router(
            point_count=200, hit_ids=["id-0", "id-1"],
        )
        # Override the cfg to a known URL
        from search.routers.shuffled_for_you import build_shuffled_for_you_router

        index_db = _fake_index_db(point_count=200)
        qdrant = _fake_qdrant(["id-0", "id-1"])
        cfg = _fake_cfg(web_ui_url="http://test:1234")
        router = build_shuffled_for_you_router(
            index_db=index_db, qdrant=qdrant, cfg=cfg,
        )
        app = _wrap(router)

        with TestClient(app) as client:
            response = client.get("/api/shuffled-for-you/feed?limit=2")
        body = response.json()
        assert body["results"][0]["id"] == "id-0"
        assert body["results"][0]["url"] == "http://test:1234/photo/id-0/raw"

    def test_session_total_reflects_pool_size(self) -> None:
        """session_total in the response is the pool size (library × top_pct)."""
        app, _, _ = self._build_router(point_count=500)
        with TestClient(app) as client:
            response = client.get("/api/shuffled-for-you/feed?top_pct=2&limit=5")
        body = response.json()
        # 500 photos × 2% = 10 candidates
        assert body["session_total"] == 10

    def test_offset_echoes_input(self) -> None:
        """page=2, limit=10 → offset=20 in the response."""
        app, _, _ = self._build_router(point_count=1000)
        with TestClient(app) as client:
            response = client.get("/api/shuffled-for-you/feed?page=2&limit=10")
        body = response.json()
        assert body["offset"] == 20
        assert body["limit"] == 10

    def test_invalidate_callback_clears_cache(self) -> None:
        """Calling invalidate_pool_cache() forces the next request to rebuild."""
        # Use fav_ids=["fav-1"] so the recommend() path is exercised
        # (cold start uses qdrant.search, not qdrant.recommend).
        app, _, qdrant = self._build_router(
            fav_ids=["fav-1"], point_count=1000,
        )
        with TestClient(app) as client:
            # First call: builds pool
            client.get("/api/shuffled-for-you/feed?limit=5")
            assert qdrant.recommend.call_count == 1

            # Same call again within TTL: cache hit, no rebuild
            client.get("/api/shuffled-for-you/feed?limit=5")
            assert qdrant.recommend.call_count == 1

            # Invalidate: next call rebuilds
            invalidate_pool_cache()
            client.get("/api/shuffled-for-you/feed?limit=5")
            assert qdrant.recommend.call_count == 2


# ===========================================================================
# 5. End-to-end with the test client (FastAPI TestClient)
# ===========================================================================


class TestRouterE2E:
    """End-to-end tests that wire the router and exercise the full
    happy path. Catches wiring mistakes the unit tests miss
    (missing include_router, wrong path, etc.)."""

    def setup_method(self) -> None:
        invalidate_pool_cache()

    def test_two_users_get_different_pools(self) -> None:
        """Different favorite signatures → different recommend() calls, different pools."""
        from search.routers.shuffled_for_you import build_shuffled_for_you_router

        # Pool size = 100 × 1% = 1 candidate per user. limit=3
        # would return 1 result, not 3. So bump point_count.
        point_count = 1000

        # First user
        index_db_1 = _fake_index_db(fav_ids=["user-1-fav"], point_count=point_count)
        qdrant_1 = _fake_qdrant([f"u1-{i}" for i in range(10)])
        cfg = _fake_cfg()
        router_1 = build_shuffled_for_you_router(
            index_db=index_db_1, qdrant=qdrant_1, cfg=cfg,
        )
        app_1 = _wrap(router_1)

        with TestClient(app_1) as client:
            body_1 = client.get("/api/shuffled-for-you/feed?limit=3").json()

        # Second user, fresh cache
        invalidate_pool_cache()
        index_db_2 = _fake_index_db(fav_ids=["user-2-fav"], point_count=point_count)
        qdrant_2 = _fake_qdrant([f"u2-{i}" for i in range(10)])
        router_2 = build_shuffled_for_you_router(
            index_db=index_db_2, qdrant=qdrant_2, cfg=cfg,
        )
        app_2 = _wrap(router_2)

        with TestClient(app_2) as client:
            body_2 = client.get("/api/shuffled-for-you/feed?limit=3").json()

        # Each user's pool has 10 ids; first 3 are returned (in
        # shuffled order — just verify they're a subset of the
        # user's full pool, and the two pools don't overlap).
        user_1_ids = {r["id"] for r in body_1["results"]}
        user_2_ids = {r["id"] for r in body_2["results"]}
        assert user_1_ids.issubset({"u1-0", "u1-1", "u1-2", "u1-3", "u1-4", "u1-5", "u1-6", "u1-7", "u1-8", "u1-9"})
        assert user_2_ids.issubset({"u2-0", "u2-1", "u2-2", "u2-3", "u2-4", "u2-5", "u2-6", "u2-7", "u2-8", "u2-9"})
        assert user_1_ids.isdisjoint(user_2_ids)
        assert len(user_1_ids) == 3
        assert len(user_2_ids) == 3
