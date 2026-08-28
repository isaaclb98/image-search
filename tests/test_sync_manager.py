"""Tests for search.sync.SyncManager — round 14.

These tests use a fake qdrant client (no network) so they exercise
the SyncManager logic in isolation.
"""

import asyncio
from unittest.mock import MagicMock


from search.sync import SyncManager


class FakeQdrant:
    """In-memory replacement for qdrant_client.QdrantClient.

    Two collections: `pending` and `search`. scroll() pages through
    `pending`, upsert() copies into `search`, delete() removes by id.
    """

    def __init__(self):
        self.pending = []
        self.search = []
        self.collections = {"pending", "search"}
        self.upsert_calls = []
        self.delete_calls = []

    def get_collection(self, name):
        if name not in self.collections:
            raise KeyError(f"collection '{name}' not found")
        return MagicMock()

    def scroll(self, collection_name, limit, offset, with_payload, with_vectors):
        if collection_name not in self.collections:
            raise KeyError(f"collection '{collection_name}' not found")
        if collection_name != "pending":
            return [], None
        # paginate by `offset`
        start = offset if offset is not None else 0
        batch = self.pending[start : start + limit]
        next_off = start + limit if start + limit < len(self.pending) else None
        return batch, next_off

    def upsert(self, collection_name, points, wait=False):
        self.upsert_calls.append((collection_name, list(points), wait))
        if collection_name != "search":
            return
        for p in points:
            # idempotent: replace existing or append
            for i, existing in enumerate(self.search):
                if existing.id == p.id:
                    self.search[i] = p
                    break
            else:
                self.search.append(p)

    def delete(self, collection_name, points_selector, wait=False):
        self.delete_calls.append((collection_name, list(points_selector), wait))
        if collection_name != "pending":
            return
        ids = set(points_selector)
        self.pending = [p for p in self.pending if p.id not in ids]


def make_record(pid, vec, payload):
    r = MagicMock()
    r.id = pid
    r.vector = vec
    r.payload = payload
    return r


def test_sync_moves_pending_to_search():
    fake = FakeQdrant()
    fake.pending = [make_record(f"p{i}", [0.0] * 1024, {"path": f"/x/{i}"}) for i in range(250)]
    mgr = SyncManager(
        qdrant=fake,
        read_collection="search",
        write_collection="pending",
        batch_size=100,
        interval_seconds=0.1,
    )

    moved = asyncio.run(mgr.sync_once())
    assert moved == 250
    assert len(fake.search) == 250
    assert len(fake.pending) == 0


def test_sync_no_op_when_pending_missing():
    fake = FakeQdrant()
    # remove "pending" so get_collection raises "Not found"
    fake.collections.discard("pending")
    mgr = SyncManager(
        qdrant=fake,
        read_collection="search",
        write_collection="pending",
    )
    moved = asyncio.run(mgr.sync_once())
    assert moved == 0
    assert fake.upsert_calls == []


def test_sync_pagination_with_small_batch():
    fake = FakeQdrant()
    fake.pending = [make_record(f"p{i}", [0.0] * 1024, {}) for i in range(37)]
    mgr = SyncManager(
        qdrant=fake,
        read_collection="search",
        write_collection="pending",
        batch_size=10,
    )
    moved = asyncio.run(mgr.sync_once())
    assert moved == 37
    assert len(fake.search) == 37
    assert len(fake.pending) == 0


def test_sync_idempotent_on_retry():
    """If the same point is in pending twice (retry scenario), the
    read collection ends up with one copy, not two."""
    fake = FakeQdrant()
    r = make_record("p1", [0.0] * 1024, {"path": "/x"})
    fake.pending = [r, r]  # duplicate (would happen if first delete failed)
    mgr = SyncManager(
        qdrant=fake,
        read_collection="search",
        write_collection="pending",
    )
    moved = asyncio.run(mgr.sync_once())
    assert moved == 2  # both were "moved" in this cycle
    # but upsert is idempotent — the read collection has 1 point
    assert len(fake.search) == 1
    assert len(fake.pending) == 0


def test_sync_isolates_collections():
    """A write to `pending` does not appear in `search` until the
    sync runs — that's the whole point of the separation."""
    fake = FakeQdrant()
    fake.pending = [make_record("p1", [0.0] * 1024, {"path": "/x"})]
    assert len(fake.search) == 0  # isolation holds
    mgr = SyncManager(
        qdrant=fake,
        read_collection="search",
        write_collection="pending",
    )
    asyncio.run(mgr.sync_once())
    assert len(fake.search) == 1  # now it appears


def test_pause_short_circuits_sync():
    """Round‑16: while paused, sync_once returns 0 immediately and
    never touches qdrant. Resuming re‑enables the loop."""
    fake = FakeQdrant()
    fake.pending = [make_record("p1", [0.0] * 1024, {"path": "/x"})]
    mgr = SyncManager(
        qdrant=fake,
        read_collection="search",
        write_collection="pending",
    )
    mgr.pause()
    moved = asyncio.run(mgr.sync_once())
    assert moved == 0
    assert fake.upsert_calls == []
    assert len(fake.search) == 0
    assert mgr.stats.paused is True

    mgr.resume()
    moved = asyncio.run(mgr.sync_once())
    assert moved == 1
    assert len(fake.search) == 1
    assert mgr.stats.paused is False
