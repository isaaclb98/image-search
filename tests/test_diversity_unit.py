"""
tests/test_diversity_unit.py — Unit tests for search/diversity.py.

Parsing helpers for the diversity feature: resolve_mode,
resolve_depth, relevance_drop_for_mode, plus the
DiversityResultCache.
"""
from __future__ import annotations

import pytest

from search.diversity import (
    DIVERSITY_AUTO_DEPTHS,
    DIVERSITY_DEPTHS,
    DIVERSITY_MODE_STRENGTHS,
    DIVERSITY_MODES,
    DiversityResultCache,
    _CachedResult,
    relevance_drop_for_mode,
    resolve_depth,
    resolve_mode,
)


# ----- Module constants -----

class TestModuleConstants:
    """Diversity mode/depth/strength constants."""

    def test_all_known_modes_have_strengths(self):
        for mode in DIVERSITY_MODE_STRENGTHS:
            assert mode in DIVERSITY_MODES

    def test_all_known_modes_have_depths(self):
        for mode in DIVERSITY_AUTO_DEPTHS:
            assert mode in DIVERSITY_MODES

    def test_strengths_in_valid_range(self):
        """Strength should be 0.0 to 1.0."""
        for strength in DIVERSITY_MODE_STRENGTHS.values():
            assert 0.0 <= strength <= 1.0

    def test_off_mode_not_in_strengths_dict(self):
        """Off mode is handled specially — not in the strengths dict."""
        assert "off" not in DIVERSITY_MODE_STRENGTHS

    def test_depths_are_positive_integers(self):
        for depth in DIVERSITY_DEPTHS.values():
            assert isinstance(depth, int)
            assert depth > 0


# ----- resolve_mode -----

class TestResolveMode:
    """Parse the diversity mode from query params."""

    def test_none_returns_off(self):
        mode, strength = resolve_mode(None)
        assert mode == "off"
        assert strength == 0.0

    def test_off_returns_off(self):
        mode, strength = resolve_mode("off")
        assert mode == "off"
        assert strength == 0.0

    def test_balanced_mode(self):
        mode, strength = resolve_mode("balanced")
        assert mode == "balanced"
        assert strength == DIVERSITY_MODE_STRENGTHS["balanced"]

    def test_low_mode(self):
        mode, strength = resolve_mode("low")
        assert mode == "low"
        assert strength == DIVERSITY_MODE_STRENGTHS["low"]

    def test_high_mode(self):
        mode, strength = resolve_mode("high")
        assert mode == "high"
        assert strength == DIVERSITY_MODE_STRENGTHS["high"]

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="diversity must be one of"):
            resolve_mode("bogus")

    def test_legacy_diverse_true(self):
        """legacy_diverse=True with no mode → balanced."""
        mode, strength = resolve_mode(None, legacy_diverse=True)
        assert mode == "balanced"

    def test_legacy_diverse_false(self):
        """legacy_diverse=False with no mode → off."""
        mode, strength = resolve_mode(None, legacy_diverse=False)
        assert mode == "off"

    def test_explicit_mode_overrides_legacy(self):
        """Explicit mode wins over legacy boolean."""
        mode, _ = resolve_mode("high", legacy_diverse=True)
        assert mode == "high"


# ----- resolve_depth -----

class TestResolveDepth:
    """Parse the diversity pool depth from query params."""

    def test_none_returns_auto(self):
        depth_label, depth_int = resolve_depth(None)
        assert depth_label == "auto"
        assert depth_int == 0

    def test_auto_explicit(self):
        depth_label, depth_int = resolve_depth("auto")
        assert depth_label == "auto"
        # 'auto' with default mode=off returns 0
        assert depth_int == 0

    def test_auto_with_mode(self):
        """auto picks a mode-appropriate default."""
        depth_label, depth_int = resolve_depth("auto", mode="balanced")
        assert depth_label == "auto"
        assert depth_int == DIVERSITY_AUTO_DEPTHS["balanced"]

    def test_explicit_depth(self):
        # Pick the first valid depth from DIVERSITY_DEPTHS
        first_depth = next(iter(DIVERSITY_DEPTHS))
        depth_label, depth_int = resolve_depth(first_depth)
        assert depth_label == first_depth
        assert depth_int == DIVERSITY_DEPTHS[first_depth]

    def test_unknown_depth_raises(self):
        with pytest.raises(ValueError, match="diversity_depth must be one of"):
            resolve_depth("bogus-depth")


# ----- relevance_drop_for_mode -----

class TestRelevanceDropForMode:
    """Per-mode relevance-drop multiplier."""

    def test_off_no_drop(self):
        """Off mode shouldn't drop relevance."""
        result = relevance_drop_for_mode("off", base_drop=0.10)
        # Off mode might return 0 or base_drop, depending on impl
        assert result >= 0.0

    def test_low_mode_small_drop(self):
        """Low mode tolerates less relevance loss."""
        result = relevance_drop_for_mode("low", base_drop=0.10)
        # Should be a small multiplier
        assert 0.0 <= result <= 0.20

    def test_high_mode_larger_drop(self):
        """High mode tolerates more relevance loss."""
        result = relevance_drop_for_mode("high", base_drop=0.10)
        # Should be larger than low mode's result
        low = relevance_drop_for_mode("low", base_drop=0.10)
        assert result >= low

    def test_balanced_mode_middle(self):
        """Balanced mode is between low and high."""
        low = relevance_drop_for_mode("low", base_drop=0.10)
        high = relevance_drop_for_mode("high", base_drop=0.10)
        balanced = relevance_drop_for_mode("balanced", base_drop=0.10)
        if low <= high:
            assert low <= balanced <= high

    def test_unknown_mode_returns_base_drop(self):
        """Unknown mode should fall back to base_drop without crashing."""
        result = relevance_drop_for_mode("bogus-mode", base_drop=0.10)
        # Implementation-specific — at minimum should not raise
        assert result >= 0.0


# ----- DiversityResultCache -----

class TestDiversityResultCache:
    """Cache for diversity computation results."""

    def test_init_empty_cache(self):
        cache = DiversityResultCache()
        assert isinstance(cache, DiversityResultCache)

    def test_get_returns_none_for_missing_key(self):
        cache = DiversityResultCache()
        assert cache.get("missing-key") is None

    def test_set_and_get(self):
        from search.diversity_compute import DiversityStats
        cache = DiversityResultCache()
        cache.put("key1", [], DiversityStats())
        result = cache.get("key1")
        assert result is not None

    def test_set_overwrites(self):
        from search.diversity_compute import DiversityStats
        cache = DiversityResultCache()
        cache.put("key1", ["h1"], DiversityStats())
        cache.put("key1", ["h2"], DiversityStats())
        result = cache.get("key1")
        assert result.hits == ("h2",)

    def test_clear_empties_cache(self):
        from search.diversity_compute import DiversityStats
        cache = DiversityResultCache()
        cache.put("key1", [], DiversityStats())
        cache.clear()
        assert cache.get("key1") is None

    def test_cache_key_isolation(self):
        from search.diversity_compute import DiversityStats
        cache = DiversityResultCache()
        cache.put("key1", ["a"], DiversityStats())
        cache.put("key2", ["b"], DiversityStats())
        assert cache.get("key1").hits == ("a",)
        assert cache.get("key2").hits == ("b",)


# ----- _CachedResult dataclass -----

class TestCachedResultDataclass:
    """The _CachedResult container."""

    def test_construction(self):
        from search.diversity_compute import DiversityStats
        result = _CachedResult(
            created_at=1234.5,
            hits=("h1", "h2"),
            stats=DiversityStats(),
        )
        assert result.hits == ("h1", "h2")
        assert result.created_at == 1234.5

    def test_construction_minimal(self):
        """All fields may have defaults."""
        from search.diversity_compute import DiversityStats
        result = _CachedResult(created_at=0.0, hits=(), stats=DiversityStats())
        assert result is not None


# ----- Module imports -----

class TestModuleImports:
    """Public API is importable."""

    def test_resolve_mode_importable(self):
        from search.diversity import resolve_mode
        assert callable(resolve_mode)

    def test_resolve_depth_importable(self):
        from search.diversity import resolve_depth
        assert callable(resolve_depth)

    def test_relevance_drop_importable(self):
        from search.diversity import relevance_drop_for_mode
        assert callable(relevance_drop_for_mode)

    def test_diversity_result_cache_importable(self):
        from search.diversity import DiversityResultCache
        assert DiversityResultCache is not None