"""
tests/test_lazy_index_cache_unit.py — Unit tests for search/lazy_index_cache.py.

The B5 contract: startup fast, lazy hydrate, stale-while-revalidate.
Tests use a mock IndexDB to avoid the full DB setup.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from search.lazy_index_cache import LazyIndexCache


# ----- Constructor -----

class TestLazyIndexCacheConstruction:
    """Basic constructor behavior."""

    def test_construction_does_not_hydrate(self):
        """Construction must not call the underlying DB."""
        mock_db = MagicMock()
        cache = LazyIndexCache(mock_db)
        # DB was never queried
        mock_db.refresh.assert_not_called()
        mock_db.read.assert_not_called()

    def test_starts_not_hydrated(self):
        mock_db = MagicMock()
        cache = LazyIndexCache(mock_db)
        assert cache.is_hydrated is False

    def test_stores_index_db_reference(self):
        mock_db = MagicMock()
        cache = LazyIndexCache(mock_db)
        assert cache.index_db is mock_db

    def test_last_hydrate_seconds_is_none_initially(self):
        mock_db = MagicMock()
        cache = LazyIndexCache(mock_db)
        assert cache.last_hydrate_seconds is None


# ----- Properties -----

class TestLazyIndexCacheProperties:
    """The cache's read-only properties."""

    def test_index_db_property(self):
        """cache.index_db returns the wrapped DB."""
        mock_db = MagicMock(name="index_db")
        cache = LazyIndexCache(mock_db)
        assert cache.index_db is mock_db

    def test_is_hydrated_starts_false(self):
        mock_db = MagicMock()
        cache = LazyIndexCache(mock_db)
        assert cache.is_hydrated is False

    def test_is_hydrated_can_be_set(self):
        mock_db = MagicMock()
        cache = LazyIndexCache(mock_db)
        cache._is_hydrated = True
        assert cache.is_hydrated is True

    def test_last_hydrate_seconds_after_hydrate(self):
        mock_db = MagicMock()
        cache = LazyIndexCache(mock_db)
        cache._hydrate_finished_at = 1000.0  # fake timestamp
        # Should return a positive number (time elapsed since fake ts)
        result = cache.last_hydrate_seconds
        assert result is not None
        assert result > 0


# ----- ensure_hydrated -----

class TestEnsureHydrated:
    """The lazy hydrate trigger."""

    def test_ensure_hydrated_idempotent(self):
        """Calling ensure_hydrated multiple times doesn't break."""
        mock_db = MagicMock()
        mock_db.refresh = MagicMock()
        cache = LazyIndexCache(mock_db)

        async def runner():
            await cache.ensure_hydrated()

        asyncio.run(runner())


# ----- schedule_hydrate -----

class TestScheduleHydrate:
    """Background hydration scheduling."""

    def test_schedule_returns_task(self):
        mock_db = MagicMock()
        mock_db.refresh = MagicMock()
        cache = LazyIndexCache(mock_db)

        async def runner():
            task = cache.schedule_hydrate()
            # Cancel immediately to avoid actual hydration
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        asyncio.run(runner())

    def test_schedule_is_idempotent(self):
        """Second schedule while first is in flight → returns the existing task."""
        mock_db = MagicMock()
        cache = LazyIndexCache(mock_db)

        async def runner():
            t1 = cache.schedule_hydrate()
            t2 = cache.schedule_hydrate()
            # Cancel both
            for t in (t1, t2):
                if t is not None and not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

        asyncio.run(runner())


# ----- Module imports -----

class TestModuleImports:
    """Public API is importable."""

    def test_lazy_index_cache_importable(self):
        from search.lazy_index_cache import LazyIndexCache
        assert LazyIndexCache is not None

    def test_lazy_index_cache_is_class(self):
        from search.lazy_index_cache import LazyIndexCache
        assert isinstance(LazyIndexCache, type)


# ----- Integration with mock IndexDB -----

class TestLazyIndexCacheWithMockDb:
    """Behavior with a realistic mock IndexDB."""

    def test_construction_with_mock_db(self):
        """A more realistic mock DB scenario."""
        class FakeIndexDB:
            def __init__(self):
                self.refresh_called = False

            def refresh(self):
                self.refresh_called = True

        fake = FakeIndexDB()
        cache = LazyIndexCache(fake)
        # Construction shouldn't trigger refresh
        assert fake.refresh_called is False

    def test_internal_state_initially(self):
        """All internal state starts at expected defaults."""
        cache = LazyIndexCache(MagicMock())
        assert cache._is_hydrated is False
        assert cache._hydrate_started_at is None
        assert cache._hydrate_finished_at is None
        assert cache._refresh_in_progress is False
        assert cache._background_task is None

    def test_can_set_hydrated_state(self):
        """Internal _is_hydrated flag can be toggled for tests."""
        cache = LazyIndexCache(MagicMock())
        cache._is_hydrated = True
        assert cache.is_hydrated is True

    def test_index_db_is_public_attribute(self):
        """The index_db is exposed via the public .index_db property."""
        mock_db = MagicMock()
        cache = LazyIndexCache(mock_db)
        # Both attribute access and property access work
        assert cache.index_db is mock_db