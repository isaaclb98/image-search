"""
tests/test_surprise_deck_unit.py

Unit tests for `search.surprise_deck.gumbel_top_k` and
`SurpriseDeckStore`. Pure-python tests; no FastAPI / Qdrant.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from search.surprise_deck import (
    DEFAULT_DECK_SIZE,
    DEFAULT_POOL_SIZE,
    DEFAULT_TEMPERATURE,
    POOL_SIZE_MAX,
    POOL_SIZE_MIN,
    SurpriseDeckCache,
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    compute_cache_key,
    gumbel_top_k,
)


# ---------------- gumbel_top_k: math ----------------


def test_gumbel_top_k_returns_k_distinct_indices():
    rng = np.random.default_rng(0)
    scores = np.linspace(0.0, 1.0, 100).tolist()
    picked = gumbel_top_k(scores, k=10, temperature=0.1, rng=rng)
    assert len(picked) == 10
    assert len(set(picked)) == 10
    assert all(0 <= i < 100 for i in picked)


def test_gumbel_top_k_reproducible_with_seeded_rng():
    scores = list(range(100))
    a = gumbel_top_k(scores, k=20, temperature=0.05, rng=np.random.default_rng(42))
    b = gumbel_top_k(scores, k=20, temperature=0.05, rng=np.random.default_rng(42))
    assert a == b


def test_gumbel_top_k_zero_temperature_is_deterministic_top_k():
    # T=0: argmax of `score + 0*g` is just argmax of score, so
    # we always get the k highest-scoring indices.
    scores = list(range(100))  # 99 is the max
    picked = gumbel_top_k(scores, k=10, temperature=0.0, rng=np.random.default_rng(0))
    # Returned in draw order; with T=0 every iteration picks the
    # same argmax until we mask it, so the order is descending by
    # score (99, 98, 97, ...).
    assert picked == list(range(99, 89, -1))


def test_gumbel_top_k_huge_temperature_is_approximately_uniform():
    # T very large: the Gumbel noise dominates the score, so the
    # sample is close to uniform over the index set. We allow a
    # generous tolerance because T=100 is not literally infinity.
    n = 1000
    rng = np.random.default_rng(0)
    scores = [0.0] * n  # all equal — the hardest case for softmax
    picked = gumbel_top_k(scores, k=200, temperature=100.0, rng=rng)
    # Each bin of 100 should have ~20 picks. Tolerate [5, 40] per bin
    # (uniform would be 20 ± sqrt(20) ≈ 20 ± 4.5; we leave a wide
    # margin because T=100 is not actually infinity).
    counts = [0] * 10
    for i in picked:
        counts[i // 100] += 1
    for c in counts:
        assert 5 <= c <= 40, f"bin count {c} outside [5, 40]"


def test_gumbel_top_k_lower_temperature_concentrates_on_top_scores():
    # Run many trials and check that low temperature picks the
    # true top-1 more often than high temperature.
    n = 100
    rng_low = np.random.default_rng(1)
    rng_high = np.random.default_rng(2)
    # Make index 0 strongly the best, and a long tail of
    # similarly-good indices.
    scores = [10.0] + [9.0 - 0.001 * i for i in range(n - 1)]
    low_wins = 0
    high_wins = 0
    trials = 2000
    for _ in range(trials):
        pick_low = gumbel_top_k(scores, k=1, temperature=0.01, rng=rng_low)
        pick_high = gumbel_top_k(scores, k=1, temperature=2.0, rng=rng_high)
        if pick_low[0] == 0:
            low_wins += 1
        if pick_high[0] == 0:
            high_wins += 1
    # Low T should win almost always; high T should win ~1/n of
    # the time (uniform on n=100).
    assert low_wins > trials * 0.8, f"low T wins {low_wins}/{trials}, expected >80%"
    # High T should win ~1/n of the time (uniform on n=100).
    # sigma of binomial(1/100, 2000) ≈ 4.45; allow 5σ to avoid
    # flake from a finite-seed rng.
    expected_high = trials / 100
    assert abs(high_wins - expected_high) < 25, (
        f"high T wins {high_wins}/{trials}, expected ~{expected_high} ±25"
    )


def test_gumbel_top_k_k_zero_returns_empty():
    assert gumbel_top_k([1.0, 2.0, 3.0], k=0, temperature=0.1, rng=np.random.default_rng(0)) == []


def test_gumbel_top_k_k_n_returns_full_permutation():
    rng = np.random.default_rng(0)
    picked = gumbel_top_k([0.1, 0.2, 0.3, 0.4, 0.5], k=5, temperature=0.1, rng=rng)
    assert sorted(picked) == [0, 1, 2, 3, 4]


def test_gumbel_top_k_rejects_k_out_of_range():
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError):
        gumbel_top_k([1.0, 2.0, 3.0], k=4, temperature=0.1, rng=rng)
    with pytest.raises(ValueError):
        gumbel_top_k([1.0, 2.0, 3.0], k=-1, temperature=0.1, rng=rng)


def test_gumbel_top_k_does_not_mutate_caller_scores():
    scores = [1.0, 2.0, 3.0, 4.0, 5.0]
    snapshot = list(scores)
    gumbel_top_k(scores, k=3, temperature=0.1, rng=np.random.default_rng(0))
    assert scores == snapshot


# ---------------- SurpriseDeckCache: session lifecycle ----------------


def test_deck_cache_put_then_get_round_trip():
    cache = SurpriseDeckCache()
    sid = cache.put("k1", ["a", "b", "c", "d"], [0.1, 0.2, 0.3, 0.4])
    result = cache.get("k1")
    assert result is not None
    got_sid, entry = result
    assert got_sid == "k1"
    assert entry.point_ids == ("a", "b", "c", "d")
    assert entry.scores == (0.1, 0.2, 0.3, 0.4)
    assert sid == "k1"  # put() returns the same key it was given


def test_deck_cache_unknown_key_returns_none():
    cache = SurpriseDeckCache()
    assert cache.get("does-not-exist") is None


def test_deck_cache_empty_key_returns_none():
    cache = SurpriseDeckCache()
    assert cache.get("") is None


def test_deck_cache_expiry_drops_entry():
    cache = SurpriseDeckCache(ttl_s=0.05)
    cache.put("k1", ["x"], [0.5])
    assert cache.get("k1") is not None
    time.sleep(0.1)
    assert cache.get("k1") is None


def test_deck_cache_clear_empties_all():
    cache = SurpriseDeckCache()
    cache.put("k1", ["a"], [0.1])
    cache.put("k2", ["b"], [0.2])
    assert len(cache) == 2
    cache.clear()
    assert len(cache) == 0


def test_deck_cache_same_key_overwrites():
    cache = SurpriseDeckCache()
    cache.put("k1", ["a", "b"], [0.1, 0.2])
    cache.put("k1", ["c", "d", "e"], [0.3, 0.4, 0.5])
    _, entry = cache.get("k1")
    assert entry.point_ids == ("c", "d", "e")
    assert entry.scores == (0.3, 0.4, 0.5)


def test_deck_cache_put_rejects_length_mismatch():
    cache = SurpriseDeckCache()
    with pytest.raises(ValueError, match="same length"):
        cache.put("k1", ["a", "b", "c"], [0.1, 0.2])


# ---------------- compute_cache_key: determinism + collision avoidance ----------------


def test_cache_key_is_deterministic_for_same_inputs():
    a = compute_cache_key(
        q="cat", positives=[], negatives=[], centroids=[], weights=None,
        collections=[], favorites=False, filename="", temperature=0.05,
        pool_size=5000, seed=1,
    )
    b = compute_cache_key(
        q="cat", positives=[], negatives=[], centroids=[], weights=None,
        collections=[], favorites=False, filename="", temperature=0.05,
        pool_size=5000, seed=1,
    )
    assert a == b
    assert len(a) == 16


def test_cache_key_differs_when_seed_differs():
    base = dict(
        q="cat", positives=[], negatives=[], centroids=[], weights=None,
        collections=[], favorites=False, filename="", temperature=0.05,
        pool_size=5000,
    )
    a = compute_cache_key(**base, seed=1)
    b = compute_cache_key(**base, seed=2)
    assert a != b


def test_cache_key_differs_when_temperature_differs():
    base = dict(
        q="cat", positives=[], negatives=[], centroids=[], weights=None,
        collections=[], favorites=False, filename="",
        pool_size=5000, seed=1,
    )
    a = compute_cache_key(**base, temperature=0.05)
    b = compute_cache_key(**base, temperature=0.10)
    assert a != b


def test_cache_key_differs_when_query_differs():
    base = dict(
        positives=[], negatives=[], centroids=[], weights=None,
        collections=[], favorites=False, filename="", temperature=0.05,
        pool_size=5000, seed=1,
    )
    a = compute_cache_key(q="cat", **base)
    b = compute_cache_key(q="dog", **base)
    assert a != b


def test_cache_key_pipe_in_value_does_not_collide():
    # ETag safety: a value containing "|" or "," must not
    # collide with two values concatenated with the same separator.
    a = compute_cache_key(
        q="a|b", positives=[], negatives=[], centroids=[], weights=None,
        collections=[], favorites=False, filename="", temperature=0.05,
        pool_size=5000, seed=1,
    )
    b = compute_cache_key(
        q="a", positives=["b"], negatives=[], centroids=[], weights=None,
        collections=[], favorites=False, filename="", temperature=0.05,
        pool_size=5000, seed=1,
    )
    assert a != b


# ---------------- Constants: reasonable ranges ----------------


def test_default_constants_are_in_valid_ranges():
    assert TEMPERATURE_MIN < DEFAULT_TEMPERATURE < TEMPERATURE_MAX
    assert POOL_SIZE_MIN <= DEFAULT_POOL_SIZE <= POOL_SIZE_MAX
    assert DEFAULT_DECK_SIZE > 0
