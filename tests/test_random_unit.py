"""
tests/test_random_unit.py — Unit tests for search/random.py.

RandomPicker is a thin async wrapper around the synchronous SQLite
random picker in IndexDB.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from search.random import RandomPicker


# ----- RandomPicker -----

class TestRandomPicker:
    """Async wrapper around IndexDB.pick_random."""

    def test_init_stores_index_db(self):
        """The constructor stores the index_db reference."""
        mock_db = MagicMock()
        picker = RandomPicker(mock_db)
        assert picker.index_db is mock_db

    def test_pick_calls_pick_random(self):
        """pick(n) should delegate to index_db.pick_random(n)."""
        mock_db = MagicMock()
        mock_db.pick_random.return_value = ["id1", "id2", "id3"]
        picker = RandomPicker(mock_db)

        async def runner():
            return await picker.pick(3)

        result = asyncio.run(runner())
        assert result == ["id1", "id2", "id3"]
        mock_db.pick_random.assert_called_once_with(3)

    def test_pick_passes_n_through(self):
        """The n argument is forwarded to pick_random."""
        mock_db = MagicMock()
        mock_db.pick_random.return_value = []
        picker = RandomPicker(mock_db)

        async def runner():
            return await picker.pick(42)

        asyncio.run(runner())
        # Verify the call was made with n=42
        mock_db.pick_random.assert_called_once_with(42)

    def test_pick_returns_empty_when_no_results(self):
        """Empty result from the DB → empty list."""
        mock_db = MagicMock()
        mock_db.pick_random.return_value = []
        picker = RandomPicker(mock_db)

        async def runner():
            return await picker.pick(10)

        result = asyncio.run(runner())
        assert result == []

    def test_pick_runs_in_thread(self):
        """pick() should use asyncio.to_thread to avoid blocking the event loop."""
        mock_db = MagicMock()
        mock_db.pick_random.return_value = ["id1"]
        picker = RandomPicker(mock_db)

        # Patch asyncio.to_thread to verify it's called
        import search.random as random_module
        original_to_thread = random_module.asyncio.to_thread
        called_with = []

        def fake_to_thread(func, *args, **kwargs):
            called_with.append((func, args, kwargs))
            return original_to_thread(func, *args, **kwargs)

        random_module.asyncio.to_thread = fake_to_thread
        try:
            async def runner():
                return await picker.pick(5)

            asyncio.run(runner())

            # asyncio.to_thread was called with the bound method
            assert len(called_with) == 1
            func, args, kwargs = called_with[0]
            # First arg is the bound method (self.index_db.pick_random)
            assert args == (5,)
        finally:
            random_module.asyncio.to_thread = original_to_thread

    def test_pick_propagates_exception(self):
        """If pick_random raises, the exception propagates through pick()."""
        mock_db = MagicMock()
        mock_db.pick_random.side_effect = RuntimeError("DB error")
        picker = RandomPicker(mock_db)

        async def runner():
            return await picker.pick(3)

        with pytest.raises(RuntimeError, match="DB error"):
            asyncio.run(runner())


# ----- Module imports -----

class TestModuleImports:
    """Public API is importable."""

    def test_random_picker_importable(self):
        from search.random import RandomPicker
        assert RandomPicker is not None

    def test_random_picker_is_class(self):
        from search.random import RandomPicker
        assert isinstance(RandomPicker, type)