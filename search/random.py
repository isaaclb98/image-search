"""
search/random.py

Thin random-picker surface backed by the search-side IndexDB cache.
"""

from __future__ import annotations

import asyncio

from search.index_db import IndexDB


class RandomPicker:
    """Async wrapper around the synchronous SQLite random picker."""

    def __init__(self, index_db: IndexDB):
        self.index_db = index_db

    async def pick(self, n: int) -> list[str]:
        return await asyncio.to_thread(self.index_db.pick_random, n)
