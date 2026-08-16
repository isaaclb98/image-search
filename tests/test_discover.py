"""
tests/test_discover.py

Layer 2 \u2014 discovery rabbithole tests using FastAPI TestClient
+ in-memory Qdrant + a small populated fixture.


"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from indexer import upsert
from indexer.upsert import VECTOR_DIM
from search import app as app_mod
from search import discover
from search.config import Config


def _seed_rounds() -> int:
    """Read the discover seed-rounds knob from the live app's Config.

    The discover constants were moved off module globals onto the
    search-side `Config` (env-driven). Tests need a live value at
    the time the test runs (after the fixture has built the app),
    so this helper reads it from `app_mod.get_cfg()` rather than
    baking the default into a module-level constant.
    """
    return app_mod.get_cfg().discover_seed_rounds


def _burst_size() -> int:
    return app_mod.get_cfg().discover_burst_size


def _recommend_overfetch() -> int:
    return app_mod.get_cfg().discover_recommend_overfetch


def _mmr_pool_size() -> int:
    return app_mod.get_cfg().discover_mmr_pool_size



# ---------------- fixtures ----------------


@pytest.fixture
def app_with_qdrant(qdrant_in_memory, nas_base, monkeypatch):
    """
    A FastAPI app wired to an in-memory Qdrant pre-populated with
    20 test points (each with a distinct mock embedding so the
    recommend math has something to do).
    """
    from PIL import Image

    from search.text_encoder import _mock_embed

    cfg = Config(
        qdrant_url="memory://",
        qdrant_collection=qdrant_in_memory.collection,
        qdrant_api_key=None,
        model_name="mock",
        model_revision="",
        device="cpu",
        top_k_default=50,
        top_k_max=200,
        query_timeout_ms=2000,
        nas_images_base=str(nas_base),
        path_prefix="",
        web_ui_url="http://localhost:8000",
        log_level="WARNING",
        test_mode=True,
    )

    client = qdrant_in_memory.client
    upsert.ensure_collection(client, qdrant_in_memory.collection, dim=VECTOR_DIM)

    # 20 points, each with a distinct mock embedding (so the
    # recommend math can actually distinguish them). All in the
    # `general` collection.
    items = []
    for i in range(20):
        pid = f"{i:032d}"  # 32-char hex, valid qdrant point id
        vec = _mock_embed(f"item_{i:02d}")
        items.append((
            pid,
            vec,
            {"id": pid, "path": str(nas_base / f"img_{i:02d}.jpg"), "collection": "general", "indexed_at": "2026-01-01T00:00:00Z"},
        ))
    upsert.upsert_batch(client, qdrant_in_memory.collection, items, wait=True)

    # Save 20 tiny JPEGs on disk so /photo/{id}/raw works.
    for i in range(20):
        Image.new("RGB", (8, 8), (i * 12, 0, 0)).save(nas_base / f"img_{i:02d}.jpg")

    app_mod.reset_for_tests()
    discover.reset_for_tests()
    app = app_mod.create_app(cfg=cfg, qdrant=qdrant_in_memory)
    with TestClient(app) as c:
        yield c
    app_mod.reset_for_tests()
    discover.reset_for_tests()


# ---------------- /api/discover/start ----------------


def test_start_returns_session_id_and_pair(app_with_qdrant):
    resp = app_with_qdrant.post("/api/discover/start")
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert isinstance(data["session_id"], str) and len(data["session_id"]) >= 16
    pair = data["pair"]
    assert pair["round"] == 1
    # The first pair is from the random pool.
    assert pair["source"] == "random"
    assert pair["left"] is not None
    assert pair["right"] is not None
    assert pair["left"]["id"] != pair["right"]["id"]
    # URLs are filled in by the route layer.
    assert pair["left"]["url"].startswith("http")
    assert pair["right"]["url"].startswith("http")


def test_start_seeded_session_persists(app_with_qdrant):
    """Two separate /api/discover/start calls give different session_ids."""
    r1 = app_with_qdrant.post("/api/discover/start").json()
    r2 = app_with_qdrant.post("/api/discover/start").json()
    assert r1["session_id"] != r2["session_id"]


# ---------------- /api/discover/pick ----------------


def _start(app) -> tuple[str, dict]:
    r = app.post("/api/discover/start")
    assert r.status_code == 200
    data = r.json()
    return data["session_id"], data["pair"]


def test_pick_records_picked_as_liked(app_with_qdrant):
    sid, pair = _start(app_with_qdrant)
    picked_id = pair["left"]["id"]
    r = app_with_qdrant.post(
        f"/api/discover/pick?session_id={sid}&image_id={picked_id}"
    )
    assert r.status_code == 200
    data = r.json()
    assert data["round"] == 1
    assert data["liked_count"] == 1
    assert data["pair"] is not None

    # Verify session state via list_liked.
    # The in-memory qdrant_search is wired into the app's _qdrant.
    qdrant = app_mod.get_qdrant()
    images = discover.list_liked(qdrant, sid, "http://localhost:8000")
    assert images is not None
    assert len(images) == 1
    assert images[0].id == picked_id
    assert images[0].picked_round == 1


def test_pick_records_other_image_as_implicit_disliked(app_with_qdrant):
    """The image NOT picked in a pair is recorded as a negative.

    Run a few rounds and verify session.disliked grows.
    """
    sid, _ = _start(app_with_qdrant)
    session = discover.get_session(sid)
    assert session is not None
    assert len(session.disliked) == 0

    # Round 1
    pair = app_with_qdrant.post(f"/api/discover/pick?session_id={sid}&image_id=00000000000000000000000000000000").json()["pair"]
    # ^ dummy id; we just want to advance the round. Real test:
    sid, pair = _start(app_with_qdrant)
    app_with_qdrant.post(
        f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
    ).json()
    # The OTHER image (right) should be in disliked.
    session = discover.get_session(sid)
    assert pair["right"]["id"] in session.disliked


def test_pick_advances_round_counter(app_with_qdrant):
    sid, pair = _start(app_with_qdrant)
    r1 = app_with_qdrant.post(
        f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
    ).json()
    assert r1["round"] == 1
    assert r1["liked_count"] == 1
    # Pick again.
    r2 = app_with_qdrant.post(
        f"/api/discover/pick?session_id={sid}&image_id={r1['pair']['right']['id']}"
    ).json()
    assert r2["round"] == 2
    assert r2["liked_count"] == 2


def test_pick_unknown_session_returns_null_pair(app_with_qdrant):
    r = app_with_qdrant.post(
        "/api/discover/pick?session_id=nonexistent&image_id=00000000000000000000000000000000"
    )
    assert r.status_code == 200
    assert r.json()["pair"] is None


def test_pick_stale_image_id_is_ignored(app_with_qdrant):
    """If the user sends a pick with an id not in the current pair, ignore."""
    sid, pair = _start(app_with_qdrant)
    # Pick a random id that isn't left or right.
    real_left = pair["left"]["id"]
    real_right = pair["right"]["id"]
    fake_id = "ffffffffffffffffffffffffffffffff"  # not in the current pair
    assert fake_id not in (real_left, real_right)
    r = app_with_qdrant.post(
        f"/api/discover/pick?session_id={sid}&image_id={fake_id}"
    ).json()
    # Round didn't advance because the pick was rejected.
    assert r["round"] == 0
    assert r["liked_count"] == 0
    # But the response still gives a (new) pair so the UI keeps moving.
    assert r["pair"] is not None


def test_seen_set_prevents_repeats(app_with_qdrant):
    """Across multiple rounds, the same image id never appears twice."""
    sid, pair = _start(app_with_qdrant)
    seen_ids: set[str] = set()
    if pair["left"]:
        seen_ids.add(pair["left"]["id"])
    if pair["right"]:
        seen_ids.add(pair["right"]["id"])
    for _ in range(10):
        if not pair:
            break
        # Always pick the left image. Get the next pair.
        next_data = app_with_qdrant.post(
            f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
        ).json()
        if next_data["pair"] is None:
            break
        pair = next_data["pair"]
        for side in ("left", "right"):
            if pair[side]:
                assert pair[side]["id"] not in seen_ids, f"image {pair[side]['id']} was shown twice"
                seen_ids.add(pair[side]["id"])


def test_pair_source_switches_to_recommend_after_seed_rounds(app_with_qdrant):
    """After seed_rounds picks, the pair source is 'recommend' (not 'random').

    The seed phase is `seed_rounds` rounds of random pairs. From
    round `seed_rounds + 1` onward, we use recommend().

    Stubs qdrant.recommend + retrieve_batch_with_vectors so the
    20-image fixture isn't exhausted by round `seed_rounds + 1`
    (11 rounds × 2 picks per round = 22 images shown, more than
    the fixture holds). Without the stub, recommend would return
    0 unseen at round 11 and the flow would fall back to random,
    masking the source transition we're testing.
    """
    seed_rounds = app_mod.get_cfg().discover_seed_rounds
    from search.qdrant_client import SearchHit

    qdrant = app_mod.get_qdrant()
    original_recommend = qdrant.recommend
    original_retrieve_vectors = qdrant.retrieve_batch_with_vectors

    def fake_recommend(positive, negative, limit=20, collections=None):
        return [
            SearchHit(id=f"r{i:032d}", path=f"/fake/r{i}.jpg", score=1.0 - i * 0.001)
            for i in range(200)
        ]

    SAME_VEC = [0.0] * 4 + [1.0]

    def fake_retrieve_batch_with_vectors(ids):
        return [(pid, list(SAME_VEC)) for pid in ids]

    qdrant.recommend = fake_recommend
    qdrant.retrieve_batch_with_vectors = fake_retrieve_batch_with_vectors
    try:
        sid, pair = _start(app_with_qdrant)
        # Round 1: random.
        assert pair["source"] == "random"
        for i in range(2, seed_rounds + 2):  # rounds 2..seed_rounds+1 (picks)
            if pair is None:
                break
            r = app_with_qdrant.post(
                f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
            ).json()
            pair = r["pair"]
            if pair is None:
                break
            if i <= seed_rounds:
                # Still in seed phase: source should be random.
                assert pair["source"] == "random", f"round {i} should be random, got {pair['source']}"
            else:
                # Past seed: should be recommend.
                assert pair["source"] == "recommend", f"round {i} should be recommend, got {pair['source']}"
    finally:
        qdrant.recommend = original_recommend
        qdrant.retrieve_batch_with_vectors = original_retrieve_vectors


def test_all_bursts_use_uniform_size(app_with_qdrant):
    """Every rabbithole burst uses _burst_size() — there is
    no first-burst special case anymore.

    After _seed_rounds() picks, the first rabbithole burst fires a
    fresh recommend. Subsequent rounds in the burst (up to
    _burst_size() total) reuse the cached pool.

    Stubs qdrant.recommend + retrieve_batch_with_vectors to feed
    200 fake unseen ids so the 20-image fixture isn't exhausted
    by round _seed_rounds()+1 (11 rounds x 2 = 22 images > 20).
    """
    from search import discover as discover_mod
    from search.qdrant_client import SearchHit

    qdrant = app_mod.get_qdrant()
    original_recommend = qdrant.recommend
    original_retrieve_vectors = qdrant.retrieve_batch_with_vectors
    original_retrieve_batch = qdrant.retrieve_batch
    call_count = [0]

    def counting_recommend(positive, negative, limit=20, collections=None):
        call_count[0] += 1
        return [
            SearchHit(id=f"r{i:032d}", path=f"/fake/r{i}.jpg", score=1.0 - i * 0.001)
            for i in range(200)
        ]

    SAME_VEC = [0.0] * 4 + [1.0]

    def fake_retrieve_batch_with_vectors(ids):
        return [(pid, list(SAME_VEC)) for pid in ids]

    def fake_retrieve_batch(ids):
        return [
            SearchHit(id=pid, path=f"/fake/{pid}.jpg", score=0.5)
            for pid in ids
        ]

    qdrant.recommend = counting_recommend
    qdrant.retrieve_batch_with_vectors = fake_retrieve_batch_with_vectors
    qdrant.retrieve_batch = fake_retrieve_batch
    try:
        # _seed_rounds() seed picks (rounds 1.._seed_rounds(); no recommend calls).
        sid, pair = _start(app_with_qdrant)
        assert pair["source"] == "random"
        for _ in range(_seed_rounds()):
            assert pair is not None
            r = app_with_qdrant.post(
                f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
            ).json()
            pair = r["pair"]
        # The last seed pick's response carries the pair for
        # round _seed_rounds()+1 — the first rabbithole round —
        # which just triggered a fresh recommend.
        assert call_count[0] == 1, f"expected 1 recommend after seed, got {call_count[0]}"
        assert pair is not None
        assert pair["source"] == "recommend"

        # The first burst is active. Verify its size is
        # _burst_size() (NOT a larger first-burst size)
        # and the counter started at 1.
        session = discover_mod.get_session(sid)
        assert session is not None
        assert session.bursts_started == 1
        assert session.current_burst_size == _burst_size()
        assert session.burst_rounds_shown == 1

        # Do more picks in the first burst. The fake recommend
        # always returns fresh ids so the burst pool stays full
        # across all _burst_size() rounds.
        for _ in range(_burst_size() - 1):
            assert pair is not None and pair["left"] is not None
            r = app_with_qdrant.post(
                f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
            ).json()
            pair = r["pair"]

        # All picks within the first burst reuse the cached pool
        # — no additional recommend calls.
        assert call_count[0] == 1, (
            f"expected still 1 recommend call (first burst not yet complete), "
            f"got {call_count[0]}"
        )
        assert session.current_burst_size == _burst_size()
    finally:
        qdrant.recommend = original_recommend
        qdrant.retrieve_batch_with_vectors = original_retrieve_vectors
        qdrant.retrieve_batch = original_retrieve_batch


def test_subsequent_bursts_use_burst_size(app_with_qdrant):
    """After the first burst completes, the second burst fires a
    fresh recommend and continues at the same _burst_size()
    (every burst is the same size now).

    Stubs qdrant.recommend and qdrant.retrieve_batch_with_vectors
    so the test isn't limited by the 20-image fixture — the stub
    returns 200 fake ids that never appear in `seen`, so the
    burst pool stays full across many rounds. This lets us drive
    the session past the first burst into the second and verify
    the burst size is consistent.
    """
    from search import discover as discover_mod
    from search.qdrant_client import SearchHit

    qdrant = app_mod.get_qdrant()
    original_recommend = qdrant.recommend
    original_retrieve_batch = qdrant.retrieve_batch
    original_retrieve_vectors = qdrant.retrieve_batch_with_vectors

    call_count = [0]

    def fake_recommend(positive, negative, limit=20, collections=None):
        call_count[0] += 1
        return [
            SearchHit(id=f"r{i:032d}", path=f"/fake/r{i}.jpg", score=1.0 - i * 0.001)
            for i in range(200)
        ]

    def fake_retrieve_batch(ids):
        return [SearchHit(id=pid, path=f"/fake/{pid}.jpg", score=0.5) for pid in ids]

    # All-same-vector stub so MMR degenerates to top-by-score.
    SAME_VEC = [0.0] * 4 + [1.0]

    def fake_retrieve_batch_with_vectors(ids):
        return [(pid, list(SAME_VEC)) for pid in ids]

    qdrant.recommend = fake_recommend
    qdrant.retrieve_batch = fake_retrieve_batch
    qdrant.retrieve_batch_with_vectors = fake_retrieve_batch_with_vectors
    try:
        # _seed_rounds() seed picks (random from the real fixture).
        sid, pair = _start(app_with_qdrant)
        for _ in range(_seed_rounds()):
            r = app_with_qdrant.post(
                f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
            ).json()
            pair = r["pair"]
        # Last seed pick's response: round _seed_rounds()+1 (start of
        # first burst), 1st recommend call.
        assert call_count[0] == 1
        session = discover_mod.get_session(sid)
        assert session is not None
        assert session.current_burst_size == _burst_size()
        assert session.bursts_started == 1

        # Drive the first burst: _burst_size() - 1 more
        # picks. None of these should trigger a fresh recommend —
        # they all reuse the cached pool.
        for _ in range(_burst_size() - 1):
            assert pair is not None
            r = app_with_qdrant.post(
                f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
            ).json()
            pair = r["pair"]
        assert call_count[0] == 1, (
            f"first burst should be {_burst_size()} rounds "
            f"with 1 recommend, got {call_count[0]}"
        )
        assert session.current_burst_size == _burst_size()
        assert session.burst_rounds_shown == _burst_size()

        # The next pick is the start of the second burst — it
        # should fire the 2nd fresh recommend and the burst size
        # stays at _burst_size() (uniform across bursts).
        r = app_with_qdrant.post(
            f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
        ).json()
        pair = r["pair"]
        assert call_count[0] == 2, (
            f"expected 2nd recommend call for the 2nd burst, got {call_count[0]}"
        )
        assert session.bursts_started == 2
        assert session.current_burst_size == _burst_size()
        assert session.burst_rounds_shown == 1  # the 2nd burst just showed its 1st round
    finally:
        qdrant.recommend = original_recommend
        qdrant.retrieve_batch = original_retrieve_batch
        qdrant.retrieve_batch_with_vectors = original_retrieve_vectors


def test_burst_pair_uses_stratified_sampling(app_with_qdrant):
    """Within a burst, the 2 images come from different strata of the pool.

    The burst sampler picks 1 image from the top third of the
    cached recommend top-N (the tightest matches) and 1 from the
    bottom third (the looser matches). This gives the user variety
    within each pair, instead of 2 nearly-identical images from
    the same top-20 band.

    The cached pool itself is now MMR-diversified — this test
    stubs `retrieve_batch_with_vectors` to give every candidate
    the SAME vector, which collapses MMR to pure relevance (no
    diversity signal). So the pool is effectively the top-20 by
    score and the stratified sampler then picks 1 from the top
    third and 1 from the bottom third.

    Stubs qdrant.recommend to return 200 fake ids in a known order
    (matches the new _recommend_overfetch()=200, since the test
    fixture has only 20 real images).
    """
    from search.qdrant_client import SearchHit

    qdrant = app_mod.get_qdrant()
    original_recommend = qdrant.recommend
    original_retrieve_batch = qdrant.retrieve_batch
    original_retrieve_batch_with_vectors = qdrant.retrieve_batch_with_vectors

    # Track every call so we can verify limit=200 (the new
    # _recommend_overfetch()).
    call_log: list[dict] = []

    def fake_recommend(positive, negative, limit=20, collections=None):
        call_log.append({"limit": limit, "positive": list(positive), "negative": list(negative)})
        return [
            SearchHit(id=f"r{i:032d}", path=f"/fake/r{i}.jpg", score=1.0 - i * 0.001)
            for i in range(200)
        ]

    def fake_retrieve_batch(ids):
        return [
            SearchHit(id=pid, path=f"/fake/{pid}.jpg", score=0.5)
            for pid in ids
        ]

    # All candidates share the same vector, so MMR degenerates to
    # pure relevance and the pool is the top-_mmr_pool_size() by
    # score. This preserves the previous "stratified sampling from
    # a top-N pool" assertion that this test was guarding.
    SAME_VEC = [0.0] * 4 + [1.0]  # unit-ish vector

    def fake_retrieve_batch_with_vectors(ids):
        return [(pid, list(SAME_VEC)) for pid in ids]

    qdrant.recommend = fake_recommend
    qdrant.retrieve_batch = fake_retrieve_batch
    qdrant.retrieve_batch_with_vectors = fake_retrieve_batch_with_vectors
    try:
        # _seed_rounds() seed picks (random from the real fixture, not stubbed).
        sid, pair = _start(app_with_qdrant)
        for _ in range(_seed_rounds()):
            r = app_with_qdrant.post(
                f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
            ).json()
            pair = r["pair"]

        # The last seed pick's response carries the pair for
        # round _seed_rounds()+1 — the first
        # burst round. The stub recommend was just called.
        assert len(call_log) == 1
        assert call_log[0]["limit"] == 200, (
            f"expected recommend limit=200 (_recommend_overfetch()), "
            f"got {call_log[0]['limit']}"
        )
        assert pair is not None
        assert pair["source"] == "recommend"

        # Both images should be from the fake r* set, with one in
        # the top third of the MMR-cached pool (_mmr_pool_size()=10,
        # so top third = positions 0-2) and one in the bottom third
        # (positions 6-9).
        def get_idx(image_id: str) -> int:
            assert image_id.startswith("r"), f"expected stubbed id, got {image_id}"
            return int(image_id[1:])

        left_idx = get_idx(pair["left"]["id"])
        right_idx = get_idx(pair["right"]["id"])

        in_top = {left_idx, right_idx} & set(range(3))
        in_bottom = {left_idx, right_idx} & set(range(6, 10))
        assert in_top, (
            f"expected one image in top third (0-2), got indices "
            f"({left_idx}, {right_idx})"
        )
        assert in_bottom, (
            f"expected one image in bottom third (6-9), got indices "
            f"({left_idx}, {right_idx})"
        )
    finally:
        qdrant.recommend = original_recommend
        qdrant.retrieve_batch = original_retrieve_batch
        qdrant.retrieve_batch_with_vectors = original_retrieve_batch_with_vectors


# ---------------- graceful fallback on recommend failure ----------------


def test_recommend_failure_falls_back_to_random(app_with_qdrant):
    """
    If qdrant.recommend raises (timeout, connection error, anything
    transient), the discovery rabbithole must NOT 500 the user. The
    QdrantSearch wrapper returns an empty result for the failed
    call; the discover flow then sees `< 2 unseen candidates` and
    falls back to a random pair. The user gets a (slightly less
    personalized) next pair, the next round retries recommend
    fresh, and the operator gets a warning log line.

    Also stubs index_db.pick_unseen + qdrant.retrieve_batch to
    feed infinite unseen ids and resolve them, so the 20-image
    fixture isn't exhausted by _seed_rounds() seed picks before we
    get to the recommend-failure round.
    """
    from search.qdrant_client import SearchHit

    qdrant = app_mod.get_qdrant()
    # Patch the *inner* client's query_points (not QdrantSearch.recommend
    # itself) so the exception raises from inside the QdrantSearch
    # wrapper's try/except. That's where the graceful-fallback lives.
    original_query_points = qdrant.client.query_points

    def boom_query_points(*args, **kwargs):
        # Simulate the production failure: qdrant-client wrapping
        # an httpx.ReadTimeout in ResponseHandlingException. This
        # is the exact exception type Isaac hit in the bug report.
        import httpcore
        from qdrant_client.http.exceptions import ResponseHandlingException
        raise ResponseHandlingException(
            httpcore.ReadTimeout("The read operation timed out")
        )

    qdrant.client.query_points = boom_query_points
    # Also stub index_db.pick_unseen to return fresh unseen ids
    # for the random fallback. Without this, the 20-image
    # fixture is exhausted by the _seed_rounds() seed picks and the
    # random fallback would return None instead of an actual
    # pair — masking the recommend-failure path we're testing.
    index_db = app_mod.get_index_db()
    original_pick_unseen = index_db.pick_unseen
    fake_counter = [0]

    def fake_pick_unseen(n, seen):
        # Always return 2 ids we haven't seen yet. Use a
        # counter so each call gets fresh ids.
        out = []
        for _ in range(n):
            candidate = f"random-{fake_counter[0]:032d}"
            fake_counter[0] += 1
            if candidate not in seen:
                out.append(candidate)
        return out

    index_db.pick_unseen = fake_pick_unseen
    # _build_pair calls retrieve_batch on the picked ids to
    # resolve them into DiscoveryImage objects. The fake random
    # ids (e.g. 'random-...') aren't in the fixture's real
    # Qdrant, so we stub retrieve_batch to return synthetic
    # SearchHits for them.
    original_retrieve_batch = qdrant.retrieve_batch

    def fake_retrieve_batch(ids):
        return [
            SearchHit(id=pid, path=f"/fake/{pid}.jpg", score=0.5)
            for pid in ids
        ]

    qdrant.retrieve_batch = fake_retrieve_batch
    try:
        # Walk through the _seed_rounds() seed rounds so we're about
        # to hit the first recommend call on round _seed_rounds()+1.
        sid, pair = _start(app_with_qdrant)
        for _ in range(_seed_rounds()):
            r = app_with_qdrant.post(
                f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
            )
            assert r.status_code == 200
            data = r.json()
            # The seed rounds are random; pair must keep flowing.
            assert data["pair"] is not None, f"seed round died: {data}"
            pair = data["pair"]

        # Round _seed_rounds()+1: this is the first recommend call.
        # The recommendation call itself will raise, the wrapper
        # returns [], the discover flow sees < 2 unseen, and
        # falls back to a random pair. The response must still
        # be 200 (not 500) and the next pair must exist.
        r6 = app_with_qdrant.post(
            f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
        )
        assert r6.status_code == 200, (
            f"recommend failure should fall back, not 500; got {r6.status_code}: {r6.text}"
        )
        data6 = r6.json()
        assert data6["pair"] is not None, (
            f"recommend failure should yield a random fallback pair, not null; got {data6}"
        )
        # The fallback pair's source is "random" (not "recommend"),
        # because the recommend call returned 0 unseen candidates
        # and the discover flow cleared the burst state.
        assert data6["pair"]["source"] == "random", (
            f"expected source='random' after recommend failure, got {data6['pair']['source']!r}"
        )
    finally:
        qdrant.client.query_points = original_query_points
        index_db.pick_unseen = original_pick_unseen
        qdrant.retrieve_batch = original_retrieve_batch


# ---------------- /discover/liked ----------------


def test_get_liked_page_renders_picks(app_with_qdrant):
    sid, pair = _start(app_with_qdrant)
    # Pick a couple to populate liked.
    app_with_qdrant.post(f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}")
    r = app_with_qdrant.get(f"/discover/liked?session_id={sid}")
    assert r.status_code == 200
    assert "your picks" in r.text
    assert pair["left"]["id"] in r.text


def test_get_liked_page_unknown_session_renders_gracefully(app_with_qdrant):
    r = app_with_qdrant.get("/discover/liked?session_id=nope")
    assert r.status_code == 200
    # Shows the "session gone" empty state with a link back.
    assert "no longer active" in r.text
    assert "/discover" in r.text


def test_list_liked_unknown_session_returns_none():
    """list_liked returns None for unknown sessions, not an empty list."""
    from qdrant_client import QdrantClient

    from search.qdrant_client import QdrantSearch
    q = QdrantSearch(client=QdrantClient(location=":memory:"), collection="x", timeout_ms=2000)
    result = discover.list_liked(q, "nonexistent")
    assert result is None


# ---------------- /discover/liked grid/feed toggle ----------------


def test_liked_page_default_view_is_grid(app_with_qdrant):
    """No ?view= -> grid classes, no feed classes."""
    sid, pair = _start(app_with_qdrant)
    app_with_qdrant.post(
        f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
    )
    r = app_with_qdrant.get(f"/discover/liked?session_id={sid}")
    body = r.text
    # Grid container, grid items, no feed container.
    assert 'class="grid"' in body
    assert 'grid-item\"' in body  # li class is now 'photo-card grid-item'
    assert 'class="feed"' not in body
    assert 'feed-item\"' not in body  # see note above
    # View toggle is rendered with grid active.
    assert "view-toggle" in body
    assert 'data-view="grid"' in body
    assert 'data-view="feed"' in body


def test_liked_page_feed_view(app_with_qdrant):
    """?view=feed -> feed classes, no grid classes. The score badge
    becomes .feed-score (matches the search page's behaviour)."""
    sid, pair = _start(app_with_qdrant)
    app_with_qdrant.post(
        f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
    )
    r = app_with_qdrant.get(f"/discover/liked?session_id={sid}&view=feed")
    body = r.text
    assert 'class="feed"' in body
    assert 'feed-item\"' in body  # li class is now 'photo-card feed-item'
    # Feed button has the active class.
    assert 'view-toggle-btn--active' in body
    # Grid classes absent.
    assert 'class="grid"' not in body
    assert 'grid-item\"' not in body  # see note above


def test_liked_page_view_toggle_links_to_other_view(app_with_qdrant):
    """The view toggle's click handlers are wired up. Server-side
    we just verify the markup is right — the JS handler is in
    discover_liked.js and is exercised by browser tests, not here."""
    sid, pair = _start(app_with_qdrant)
    app_with_qdrant.post(
        f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
    )
    # In grid mode, only the grid button carries --active.
    r = app_with_qdrant.get(f"/discover/liked?session_id={sid}")
    body = r.text
    # Walk the two toggle buttons and check which one is active.
    import re
    grid_btn = re.search(
        r'<button[^>]*data-view="grid"[^>]*>', body,
    )
    feed_btn = re.search(
        r'<button[^>]*data-view="feed"[^>]*>', body,
    )
    assert grid_btn and feed_btn, "view toggle buttons missing"
    assert "view-toggle-btn--active" in grid_btn.group(0)
    assert "view-toggle-btn--active" not in feed_btn.group(0)

    # In feed mode, the inverse is true.
    r2 = app_with_qdrant.get(f"/discover/liked?session_id={sid}&view=feed")
    body2 = r2.text
    grid_btn2 = re.search(
        r'<button[^>]*data-view="grid"[^>]*>', body2,
    )
    feed_btn2 = re.search(
        r'<button[^>]*data-view="feed"[^>]*>', body2,
    )
    assert "view-toggle-btn--active" in feed_btn2.group(0)
    assert "view-toggle-btn--active" not in grid_btn2.group(0)


def test_liked_page_invalid_view_falls_back_to_grid(app_with_qdrant):
    """Unknown ?view= values coerce to grid (same as the search page)."""
    sid, pair = _start(app_with_qdrant)
    app_with_qdrant.post(
        f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
    )
    r = app_with_qdrant.get(f"/discover/liked?session_id={sid}&view=wat")
    body = r.text
    # Falls back to grid.
    assert 'class="grid"' in body
    assert 'grid-item\"' in body  # li class is now 'photo-card grid-item'
    assert 'class="feed"' not in body
    # The toggle's grid button is the active one.
    import re
    grid_btn = re.search(
        r'<button[^>]*data-view="grid"[^>]*>', body,
    )
    assert "view-toggle-btn--active" in grid_btn.group(0)


def test_liked_page_loads_discover_liked_js(app_with_qdrant):
    """The view-toggle + copy-paths controller script is included
    on /discover/liked."""
    sid, pair = _start(app_with_qdrant)
    r = app_with_qdrant.get(f"/discover/liked?session_id={sid}")
    assert "discover_liked" in r.text


def test_liked_page_session_gone_hides_view_toggle(app_with_qdrant):
    """When the session is gone, the view toggle isn't useful (no
    images to view). It should NOT render in that empty state —
    the user is bounced back to /discover instead."""
    r = app_with_qdrant.get("/discover/liked?session_id=nope&view=feed")
    # No images -> the actions row (with the toggle) isn't rendered.
    # The grid container is also not rendered.
    assert "view-toggle" not in r.text
    assert 'class="feed"' not in r.text
    assert 'class="grid"' not in r.text


def test_liked_page_tiles_have_data_path(app_with_qdrant):
    """Each tile carries data-path so the copy-paths JS can grab
    the underlying file path (one per line, pick order)."""
    sid, pair = _start(app_with_qdrant)
    app_with_qdrant.post(
        f"/api/discover/pick?session_id={sid}&image_id={pair['left']['id']}"
    )
    r = app_with_qdrant.get(f"/discover/liked?session_id={sid}")
    body = r.text
    # Find the tile for the picked image and verify data-path is
    # populated (not the bare /photo/<id> URL fallback).
    assert "data-path=" in body
    # Pick a tile's data-path and verify it's a real path (not /photo/...).
    import re
    paths = re.findall(r'data-path="([^"]+)"', body)
    assert paths, "no data-path attributes found"
    assert all(not p.startswith("/photo/") for p in paths), (
        f"data-path fell back to photo URL: {paths}"
    )


# ---------------- /discover page ----------------


def test_get_discover_page_renders(app_with_qdrant):
    r = app_with_qdrant.get("/discover")
    assert r.status_code == 200
    assert "discover-pair" in r.text
    assert "done \u2192" in r.text or "done &rarr;" in r.text
    assert "discover" in r.text


def test_seed_phase_uses_index_db_pick_unseen():
    from search.qdrant_client import SearchHit

    class FakeIndexDB:
        def __init__(self):
            self.calls = []

        def pick_unseen(self, n, exclude):
            self.calls.append((n, set(exclude)))
            return ["seed-a", "seed-b"]

    class FakeQdrant:
        random_window_called = False

        def random_window(self, limit=20):
            self.random_window_called = True
            return []

        def retrieve_batch(self, ids):
            return [SearchHit(id=pid, path=f"/fake/{pid}.jpg", score=0.0) for pid in ids]

    discover.reset_for_tests()
    index_db = FakeIndexDB()
    qdrant = FakeQdrant()
    try:
        _opts = discover.DiscoverOptions(
            seed_rounds=10,
            recommend_overfetch=200,
            diversify_lambda=0.5,
            mmr_pool_size=10,
            burst_size=5,
            session_ttl_seconds=1800,
        )
        _sid, pair = discover.start_session(qdrant, _opts, index_db)
        assert pair.source == "random"
        assert {pair.left.id, pair.right.id} == {"seed-a", "seed-b"}
        assert index_db.calls
        assert qdrant.random_window_called is False
    finally:
        discover.reset_for_tests()


# ---------------- random_window ----------------


def test_random_window_samples_uniformly(qdrant_in_memory, nas_base):
    """random_window returns a uniform random sample, not a clump.

    The previous implementation used an integer offset for
    scroll, which the in-memory qdrant-client ignores, so the
    function always returned the same first-N points regardless
    of the random value. The current implementation paginates
    to gather all ids, then uniformly samples.
    """
    from indexer import upsert
    from indexer.upsert import VECTOR_DIM
    from search.text_encoder import _mock_embed

    client = qdrant_in_memory.client
    upsert.ensure_collection(client, qdrant_in_memory.collection, dim=VECTOR_DIM)
    # 50 distinct points, each with its own id and payload.
    items = []
    for i in range(50):
        pid = f"{i:032d}"
        vec = _mock_embed(f"item_{i:02d}")
        items.append((
            pid,
            vec,
            {"id": pid, "path": str(nas_base / f"img_{i:02d}.jpg"), "collection": "general", "indexed_at": "2026-01-01T00:00:00Z"},
        ))
    upsert.upsert_batch(client, qdrant_in_memory.collection, items, wait=True)

    # Call random_window many times and check that the union
    # of returned ids covers the whole collection. The old
    # (broken) implementation would have only ever returned the
    # first ~20 ids in insertion order, never reaching coverage
    # close to 50.
    seen_ids: set[str] = set()
    for _ in range(30):
        hits = qdrant_in_memory.random_window(limit=10)
        for h in hits:
            seen_ids.add(h.id)
    # 30 calls * 10 hits = 300 draws with replacement. With
    # uniform sampling from 50 ids, the expected unique coverage
    # is ~50 * (1 - (1 - 10/50)^30) ~= 50. The old broken
    # implementation would top out at the first 20 ids.
    assert len(seen_ids) >= 40, (
        f"random_window is not sampling uniformly across the "
        f"collection — only saw {len(seen_ids)} of 50 ids"
    )


# ---------------- MMR diversification ----------------


def _orthonormal_vecs(n: int, dim: int = 32) -> list[list[float]]:
    """Return n mutually orthogonal unit vectors in R^dim. Useful
    for setting up a candidate pool where every candidate is
    maximally diverse from every other candidate.
    """
    import random
    rng = random.Random(0xC0FFEE)  # noqa: S311 - test fixture
    assert dim >= n
    vecs = [[rng.gauss(0, 1) for _ in range(dim)] for _ in range(n)]
    out: list[list[float]] = []
    for v in vecs:
        for u in out:
            dot = sum(a * b for a, b in zip(v, u, strict=False))
            v = [a - dot * b for a, b in zip(v, u, strict=False)]
        norm = sum(a * a for a in v) ** 0.5
        if norm == 0:
            v = [0.0] * dim
            v[0] = 1.0
            norm = 1.0
        out.append([a / norm for a in v])
    return out


def test_mmr_select_returns_diverse_subset():
    """Unit test for the MMR selector. Given 20 candidates whose
    vectors span 5 distinct clusters (4 near-duplicates per
    cluster), MMR with k=10 should pick one from each cluster
    rather than the top-10 by relevance alone.
    """
    from search.discover import _mmr_select

    n_clusters = 5
    per_cluster = 4
    n_clusters * per_cluster  # 20
    vecs = _orthonormal_vecs(n_clusters, dim=64)
    candidates: list[tuple[str, float, list[float]]] = []
    for c in range(n_clusters):
        for j in range(per_cluster):
            score = 0.9 - c * 0.05 + j * 0.001
            candidates.append((f"c{c}_j{j}", score, list(vecs[c])))

    picked = _mmr_select(candidates, k=10, lambda_=0.5)
    assert len(picked) == 10
    # First pick is highest-scoring (c0_j3). After that, each
    # next pick should go to a different cluster than anything
    # already chosen. So the first 5 picks should span all 5
    # clusters.
    clusters_covered = {p.split("_")[0] for p in picked[:5]}
    assert clusters_covered == {f"c{c}" for c in range(n_clusters)}, (
        f"MMR didn't diversify across clusters in first 5 picks: "
        f"{picked[:5]}"
    )


def test_mmr_lambda_zero_collapses_to_top_by_score():
    """Sanity check on the lambda knob. With LAMBDA=0, MMR
    degenerates to 'pick the k highest-scoring candidates,' which
    is the previous (clustered) behaviour. Useful for A/B testing
    the relevance/diversity trade-off.
    """
    from search.discover import _mmr_select

    n = 20
    vecs = _orthonormal_vecs(n, dim=64)
    candidates = [
        (f"id{i}", 1.0 - i * 0.01, list(vecs[i])) for i in range(n)
    ]
    picked = _mmr_select(candidates, k=5, lambda_=0.0)
    assert picked == [f"id{i}" for i in range(5)]


def test_mmr_lambda_one_collapses_to_diversity_only():
    """With LAMBDA=1, relevance contributes nothing and MMR
    maximises pairwise distance — second-and-later picks should
    each be the candidate furthest from everything already chosen.
    """
    from search.discover import _mmr_select

    dim = 32
    v0 = [0.0] * dim
    v0[0] = 1.0
    v1 = [0.0] * dim
    v1[1] = 1.0
    candidates = []
    for i in range(10):
        candidates.append((f"a{i}", 0.5, list(v0)))
    for i in range(10):
        candidates.append((f"b{i}", 0.5, list(v1)))

    picked = _mmr_select(candidates, k=2, lambda_=1.0)
    assert len(picked) == 2
    first_cluster = "a" if picked[0].startswith("a") else "b"
    second_cluster = "a" if picked[1].startswith("a") else "b"
    assert first_cluster != second_cluster, (
        f"with LAMBDA=1, MMR should span both clusters in 2 "
        f"picks; got {picked}"
    )


def test_burst_pool_spans_clusters(app_with_qdrant):
    """Integration test: stub recommend() to return 30 photos
    clustered into 3 groups, then verify that the burst pool
    after MMR spans all 3 groups rather than being 20 near-
    duplicates of the top-2 groups. This guards the actual fix
    for Isaac's reported issue: the recommendations are too
    similar, I get many of nearly the same photo.
    """
    from search.qdrant_client import SearchHit

    qdrant = app_mod.get_qdrant()
    original_recommend = qdrant.recommend
    original_retrieve_vectors = qdrant.retrieve_batch_with_vectors

    def fake_recommend(positive, negative, limit=20, collections=None):
        hits = []
        for cluster in range(3):
            for idx in range(10):
                hits.append(SearchHit(
                    id=f"c{cluster}_i{idx:02d}",
                    path=f"/fake/c{cluster}_i{idx:02d}.jpg",
                    score=0.9 - cluster * 0.1 - idx * 0.001,
                ))
        return hits[:limit]

    def _vec(cluster: int, idx: int) -> list[float]:
        import random
        rng = random.Random(cluster * 1000 + idx)  # noqa: S311 - test fixture
        # Three orthogonal-ish directions; jitter within a
        # cluster is small, jitter between clusters is large.
        base = [0.0] * VECTOR_DIM
        base[cluster] = 1.0
        noise_scale = 0.05
        v = [
            base[d] + (rng.gauss(0, noise_scale) if d < 3 else 0.0)
            for d in range(VECTOR_DIM)
        ]
        norm = sum(a * a for a in v) ** 0.5
        return [a / norm for a in v]

    def fake_retrieve_batch_with_vectors(ids):
        out = []
        for pid in ids:
            cluster = int(pid[1])
            idx = int(pid.split("_i")[1])
            out.append((pid, _vec(cluster, idx)))
        return out

    qdrant.recommend = fake_recommend
    qdrant.retrieve_batch_with_vectors = fake_retrieve_batch_with_vectors
    try:
        sid, pair = _start(app_with_qdrant)
        # _seed_rounds() seed picks to transition out of the random phase.
        for _ in range(_seed_rounds()):
            r = app_with_qdrant.post(
                f"/api/discover/pick?session_id={sid}"
                f"&image_id={pair['left']['id']}"
            ).json()
            pair = r["pair"]
        # Burst just started; expose the cached pool via the
        # session dict.
        from search import discover as discover_mod
        session = discover_mod.get_session(sid)
        assert session is not None
        pool = session.burst_pool
        assert len(pool) == 10, f"expected pool of 10, got {len(pool)}"
        clusters_in_pool = {pid.split("_")[0] for pid in pool}
        assert clusters_in_pool == {"c0", "c1", "c2"}, (
            f"MMR pool doesn't span all 3 clusters — got "
            f"{sorted(clusters_in_pool)}. Without diversity, the "
            f"pool would be 20 items from c0/c1 only."
        )
    finally:
        qdrant.recommend = original_recommend
        qdrant.retrieve_batch_with_vectors = original_retrieve_vectors
