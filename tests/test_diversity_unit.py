"""
tests/test_diversity_unit.py — Tests for the diversity service surface
that survives the native MMR branch.

The legacy `resolve_mode` / `resolve_depth` / `relevance_drop_for_mode`
helpers and the per-mode `DIVERSITY_*` constant tables are deleted on
this branch. What stays:

  - `resolve_diversity(value)` — parse and validate a float in [0.0, 1.0]
  - `resolve_pool_depth(value, *, max_pool_depth)` — parse and clamp an int
  - `DiversityResultCache` — unchanged LRU+TTL cache

This file pins those.
"""
from __future__ import annotations

import time

import pytest

from search.diversity import (
    DiversityResultCache,
    resolve_diversity,
    resolve_pool_depth,
)


# ---------------------------------------------------------------------------
# resolve_diversity
# ---------------------------------------------------------------------------


class TestResolveDiversity:
    def test_none_returns_default(self):
        # Default is 0.5 (balanced) on the native MMR branch.
        assert resolve_diversity(None) == 0.5

    def test_numeric_in_range(self):
        assert resolve_diversity(0.0) == 0.0
        assert resolve_diversity(0.5) == 0.5
        assert resolve_diversity(1.0) == 1.0
        assert resolve_diversity(0.7) == 0.7

    def test_string_numeric_parsed(self):
        assert resolve_diversity("0.7") == 0.7
        assert resolve_diversity("0") == 0.0
        assert resolve_diversity("1") == 1.0

    def test_string_non_numeric_raises(self):
        with pytest.raises(ValueError, match="diversity must be a float"):
            resolve_diversity("balanced")  # legacy enum value, no longer valid

    def test_below_zero_raises(self):
        with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
            resolve_diversity(-0.1)

    def test_above_one_raises(self):
        with pytest.raises(ValueError, match=r"\[0\.0, 1\.0\]"):
            resolve_diversity(1.5)

    def test_wrong_type_raises(self):
        with pytest.raises(TypeError, match="diversity must be a float"):
            resolve_diversity([0.5])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# resolve_pool_depth
# ---------------------------------------------------------------------------


class TestResolvePoolDepth:
    def test_none_returns_max(self):
        # No user-provided depth → use server-side ceiling.
        assert resolve_pool_depth(None, max_pool_depth=5000) == 5000

    def test_int_in_range(self):
        assert resolve_pool_depth(500, max_pool_depth=5000) == 500
        assert resolve_pool_depth(5000, max_pool_depth=5000) == 5000

    def test_string_int_parsed(self):
        assert resolve_pool_depth("2000", max_pool_depth=5000) == 2000

    def test_above_max_clamps(self):
        # Values above ceiling are clamped silently (server-side cap).
        assert resolve_pool_depth(10000, max_pool_depth=5000) == 5000

    def test_below_one_raises(self):
        with pytest.raises(ValueError, match=">= 1"):
            resolve_pool_depth(0, max_pool_depth=5000)

    def test_negative_raises(self):
        with pytest.raises(ValueError, match=">= 1"):
            resolve_pool_depth(-1, max_pool_depth=5000)

    def test_non_numeric_string_raises(self):
        with pytest.raises(ValueError, match=">= 1"):
            resolve_pool_depth("balanced", max_pool_depth=5000)  # legacy enum

    def test_bool_raises(self):
        # Defensive: booleans aren't valid depths.
        with pytest.raises(TypeError, match=">= 1"):
            resolve_pool_depth(True, max_pool_depth=5000)


# ---------------------------------------------------------------------------
# DiversityResultCache
# ---------------------------------------------------------------------------


class TestDiversityResultCache:
    def test_init_empty_cache(self):
        cache = DiversityResultCache(max_entries=64, ttl_seconds=300)
        assert len(cache) == 0

    def test_put_then_get(self):
        cache = DiversityResultCache(max_entries=64, ttl_seconds=300)
        hits = ["a", "b", "c"]
        stats = type("S", (), {"applied": True, "diversity": 0.5})()
        cache.put("k1", hits, stats)  # type: ignore[arg-type]
        result = cache.get("k1")
        assert result is not None
        assert result.hits == hits
        assert result.stats is stats

    def test_get_missing_returns_none(self):
        cache = DiversityResultCache(max_entries=64, ttl_seconds=300)
        assert cache.get("missing") is None

    def test_ttl_expiry(self):
        # ttl=0 means everything is expired immediately.
        cache = DiversityResultCache(max_entries=64, ttl_seconds=0)
        stats = type("S", (), {"applied": True, "diversity": 0.5})()
        cache.put("k1", ["a"], stats)  # type: ignore[arg-type]
        time.sleep(0.01)
        assert cache.get("k1") is None

    def test_empty_put_is_noop(self):
        cache = DiversityResultCache(max_entries=64, ttl_seconds=300)
        # Empty hits + stats.applied=False → don't cache (avoid wasting
        # an entry on a degenerate result).
        from search.diversity_compute import DiversityStats
        stats = DiversityStats(requested=True, applied=False)
        cache.put("k1", [], stats)
        assert cache.get("k1") is None

    def test_lru_eviction(self):
        # max_entries=2 → 3rd put evicts the oldest.
        cache = DiversityResultCache(max_entries=2, ttl_seconds=300)
        stats = type("S", (), {"applied": True, "diversity": 0.5})()
        cache.put("k1", ["a"], stats)  # type: ignore[arg-type]
        cache.put("k2", ["b"], stats)  # type: ignore[arg-type]
        cache.put("k3", ["c"], stats)  # type: ignore[arg-type]
        assert cache.get("k1") is None  # evicted
        assert cache.get("k2") is not None
        assert cache.get("k3") is not None

    def test_clear(self):
        cache = DiversityResultCache(max_entries=64, ttl_seconds=300)
        stats = type("S", (), {"applied": True, "diversity": 0.5})()
        cache.put("k1", ["a"], stats)  # type: ignore[arg-type]
        cache.clear()
        assert len(cache) == 0
        assert cache.get("k1") is None
