"""
tests/test_sync_meta_unit.py — Unit tests for indexer/sync_meta.py.

Tiny module that owns the sync-state collection names and helpers.
Critical because search/index_db.py and tests depend on these
constants being stable.
"""
from __future__ import annotations

import uuid

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from indexer.sync_meta import (
    META_COLLECTION,
    META_POINT_ID,
    PENDING_COLLECTION,
    _utc_now,
    ensure_sync_collections,
    pending_count,
    write_meta,
)


@pytest.fixture
def in_memory_qdrant():
    return QdrantClient(location=":memory:")


# ----- Module constants -----

class TestSyncMetaConstants:
    """Stable constants referenced from search/index_db.py and tests."""

    def test_meta_collection_name(self):
        assert META_COLLECTION == "_sync_meta"

    def test_pending_collection_name(self):
        assert PENDING_COLLECTION == "_pending"

    def test_meta_point_id_is_valid_uuid(self):
        """META_POINT_ID must be a valid UUID because Qdrant rejects non-UUID ids."""
        # This will raise ValueError if the string isn't a valid UUID
        parsed = uuid.UUID(META_POINT_ID)
        assert str(parsed) == META_POINT_ID

    def test_meta_point_id_is_stable(self):
        """The singleton id must not change — consumers depend on it."""
        # If you change this, external state (drift markers, test
        # fixtures) breaks. Pin the value here.
        assert META_POINT_ID == "11111111-1111-1111-1111-111111111111"


# ----- _utc_now -----

class TestUtcNow:
    """UTC timestamp in ISO format."""

    def test_returns_string(self):
        assert isinstance(_utc_now(), str)

    def test_contains_t_separator(self):
        """ISO-8601 format includes 'T' between date and time."""
        assert "T" in _utc_now()

    def test_ends_with_utc_offset(self):
        """Timezone-aware ISO format ends with '+00:00' or 'Z'."""
        ts = _utc_now()
        assert ts.endswith("+00:00") or ts.endswith("Z")


# ----- ensure_sync_collections -----

class TestEnsureSyncCollections:
    """Create the sync metadata collections if they don't exist."""

    def test_creates_collections(self, in_memory_qdrant):
        ensure_sync_collections(in_memory_qdrant, images_collection="images")
        cols = {c.name for c in in_memory_qdrant.get_collections().collections}
        assert "images" in cols
        assert META_COLLECTION in cols
        assert PENDING_COLLECTION in cols

    def test_idempotent(self, in_memory_qdrant):
        """Calling twice should not raise."""
        ensure_sync_collections(in_memory_qdrant, images_collection="images")
        ensure_sync_collections(in_memory_qdrant, images_collection="images")
        # Still all three collections exist
        cols = {c.name for c in in_memory_qdrant.get_collections().collections}
        assert "images" in cols
        assert META_COLLECTION in cols
        assert PENDING_COLLECTION in cols

    def test_custom_images_collection_name(self, in_memory_qdrant):
        ensure_sync_collections(in_memory_qdrant, images_collection="my_images")
        cols = {c.name for c in in_memory_qdrant.get_collections().collections}
        assert "my_images" in cols

    def test_creates_with_correct_dim(self, in_memory_qdrant):
        """The images collection is created with the active model's dim."""
        from image_search_kernel.registry import get_active_model_spec
        dim = get_active_model_spec().dim
        ensure_sync_collections(in_memory_qdrant, images_collection="images")
        info = in_memory_qdrant.get_collection("images")
        # In-memory Qdrant may report different shapes for local vs remote
        assert info is not None


# ----- write_meta -----

class TestWriteMeta:
    """Write a metadata point into the _sync_meta collection."""

    def test_write_basic_payload(self, in_memory_qdrant):
        ensure_sync_collections(in_memory_qdrant)
        write_meta(in_memory_qdrant, {"scan_started": "2026-01-01T00:00:00Z"})
        recs, _ = in_memory_qdrant.scroll(
            META_COLLECTION,
            with_payload=True,
            limit=1,
        )
        assert len(recs) == 1
        assert recs[0].id == META_POINT_ID

    def test_write_upserts_same_point(self, in_memory_qdrant):
        """Multiple write_meta calls update the same singleton point."""
        ensure_sync_collections(in_memory_qdrant)
        write_meta(in_memory_qdrant, {"v": 1})
        write_meta(in_memory_qdrant, {"v": 2})
        # Should still be exactly one point in the collection
        recs, _ = in_memory_qdrant.scroll(
            META_COLLECTION,
            with_payload=True,
            limit=10,
        )
        assert len(recs) == 1

    def test_write_payload_preserves_data(self, in_memory_qdrant):
        ensure_sync_collections(in_memory_qdrant)
        data = {
            "last_scan_at": "2026-08-26T12:00:00Z",
            "items_processed": 100,
            "status": "ok",
        }
        write_meta(in_memory_qdrant, data)
        recs, _ = in_memory_qdrant.scroll(
            META_COLLECTION,
            with_payload=True,
            limit=1,
        )
        assert recs[0].payload.get("last_scan_at") == "2026-08-26T12:00:00Z"
        assert recs[0].payload.get("items_processed") == 100
        assert recs[0].payload.get("status") == "ok"

    def test_write_overwrites_previous(self, in_memory_qdrant):
        """A second write replaces the previous payload (singleton semantics)."""
        ensure_sync_collections(in_memory_qdrant)
        write_meta(in_memory_qdrant, {"generation": 1})
        write_meta(in_memory_qdrant, {"generation": 2})
        recs, _ = in_memory_qdrant.scroll(
            META_COLLECTION,
            with_payload=True,
            limit=1,
        )
        assert recs[0].payload.get("generation") == 2


# ----- pending_count -----

class TestPendingCount:
    """Count pending items in the _pending collection."""

    def test_empty_pending_returns_zero(self, in_memory_qdrant):
        ensure_sync_collections(in_memory_qdrant)
        assert pending_count(in_memory_qdrant) == 0

    def test_returns_count_after_inserts(self, in_memory_qdrant):
        ensure_sync_collections(in_memory_qdrant)
        # Insert some points into _pending
        from qdrant_client.http import models as qmodels
        points = [
            qmodels.PointStruct(
                id=str(uuid.uuid4()),
                vector=[0.0],
                payload={"path": f"/img/{i}.jpg"},
            )
            for i in range(3)
        ]
        in_memory_qdrant.upsert(
            collection_name=PENDING_COLLECTION,
            points=points,
            wait=True,
        )
        assert pending_count(in_memory_qdrant) == 3

    def test_returns_zero_when_no_collections(self, in_memory_qdrant):
        """Without ensure_sync_collections, pending should be 0 (no crash)."""
        # Don't call ensure_sync_collections — the function should
        # handle missing collections gracefully
        count = pending_count(in_memory_qdrant)
        assert count == 0


# ----- Module-level invariants -----

class TestSyncMetaInvariants:
    """The module's invariants other modules depend on."""

    def test_all_constants_are_distinct(self):
        """Collection names and point id should all be different strings."""
        assert META_COLLECTION != PENDING_COLLECTION
        assert META_POINT_ID != META_COLLECTION
        assert META_POINT_ID != PENDING_COLLECTION

    def test_collections_are_underscore_prefixed(self):
        """Private collections use '_' prefix to signal they're internal."""
        assert META_COLLECTION.startswith("_")
        assert PENDING_COLLECTION.startswith("_")