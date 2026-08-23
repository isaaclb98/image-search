"""
tests/test_migrate.py — schema-version migration helper.

The migration helper (§A2 of the plan) is the bridge between legacy
unversioned points and the versioned schema. These tests exercise the
contract end-to-end against in-memory Qdrant: a v0 collection with a
synthetic dataset is migrated to v1 and the resulting collection is
verified to satisfy the v1 contract.
"""

from __future__ import annotations

import time
import uuid
from pathlib import PurePosixPath

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels


@pytest.fixture
def qdrant_in_memory_pair():
    """Two in-memory Qdrant clients (source + target) for migration tests."""
    src = QdrantClient(location=":memory:")
    tgt = QdrantClient(location=":memory:")
    yield src, tgt
    del src, tgt


def _seed_v0_collection(client: QdrantClient, name: str) -> list[str]:
    """Create a collection of synthetic v0 points and return their ids."""
    from image_search_kernel.payload_schema import (
        FIELD_BLURHASH,
        FIELD_ID,
        FIELD_PATH,
    )

    dim = 4  # tiny for fast tests
    if name not in {c.name for c in client.get_collections().collections}:
        client.create_collection(
            collection_name=name,
            vectors_config=qmodels.VectorParams(
                size=dim, distance=qmodels.Distance.COSINE,
            ),
        )

    paths = [
        "/photos/vacation/img_001.jpg",
        "/photos/vacation/img_002.jpg",
        "/photos/portraits/sub/face.jpg",
        "/photos/top_level.png",
    ]
    points: list[qmodels.PointStruct] = []
    ids: list[str] = []
    for i, path in enumerate(paths):
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"v0-{path}"))
        ids.append(point_id)
        # v0 payloads have id, path, blurhash, but no folder, no
        # model_dim, no _schema_version.
        payload = {
            FIELD_ID: point_id,
            FIELD_PATH: path,
            FIELD_BLURHASH: None,
        }
        # unit-norm vector
        vec = [0.0] * dim
        vec[i] = 1.0
        points.append(qmodels.PointStruct(
            id=point_id, vector=vec, payload=payload,
        ))
    client.upsert(collection_name=name, points=points)
    return ids


def test_migrate_v0_to_v1_produces_folder_and_model_dim(qdrant_in_memory_pair):
    """End-to-end: v0 → v1 migration populates `folder` and `model_dim`."""
    from image_search_kernel.migrate import (
        ModelMeta,
        make_v0_to_v1_transforms,
        migrate_collection,
    )
    from image_search_kernel.payload_schema import (
        FIELD_FOLDER,
        FIELD_MODEL_DIM,
        FIELD_PATH,
        FIELD_SCHEMA_VERSION,
        SCHEMA_VERSION,
    )

    src, tgt = qdrant_in_memory_pair
    seeded_ids = _seed_v0_collection(src, "v0_test")

    transforms = make_v0_to_v1_transforms()
    model_meta = ModelMeta(name="test", dim=4, revision="r0")
    report = migrate_collection(
        source=src,
        target=tgt,
        source_collection="v0_test",
        target_collection="v1_test",
        target_version=SCHEMA_VERSION,
        model_meta=model_meta,
        field_transforms=transforms,
        vector_strategy="copy",
        batch_size=64,
    )

    # All points read and written, no failures.
    assert report.total_read == len(seeded_ids)
    assert report.total_written == len(seeded_ids)
    assert report.total_failed == 0
    assert report.target_version == SCHEMA_VERSION
    assert report.vector_strategy == "copy"

    # Verify the target collection contains the migrated points with
    # the expected fields.
    out_points, _ = tgt.scroll(
        collection_name="v1_test",
        with_payload=True,
        with_vectors=True,
        limit=100,
    )
    by_path = {p.payload[FIELD_PATH]: p for p in out_points}
    assert set(by_path.keys()) == {
        "/photos/vacation/img_001.jpg",
        "/photos/vacation/img_002.jpg",
        "/photos/portraits/sub/face.jpg",
        "/photos/top_level.png",
    }

    # folder is derived from path.parent (PurePosixPath).
    assert by_path["/photos/vacation/img_001.jpg"].payload[FIELD_FOLDER] == (
        str(PurePosixPath("/photos/vacation/img_001.jpg").parent)
    )
    assert by_path["/photos/top_level.png"].payload[FIELD_FOLDER] == (
        str(PurePosixPath("/photos/top_level.png").parent)
    )

    # model_dim sourced from registry-resolved ModelMeta.
    for p in out_points:
        assert p.payload[FIELD_MODEL_DIM] == 4

    # _schema_version set to SCHEMA_VERSION.
    for p in out_points:
        assert p.payload[FIELD_SCHEMA_VERSION] == SCHEMA_VERSION


def test_migrate_copies_vectors_unchanged(qdrant_in_memory_pair):
    """vector_strategy='copy' preserves the original vectors verbatim."""
    from image_search_kernel.migrate import (
        ModelMeta,
        make_v0_to_v1_transforms,
        migrate_collection,
    )
    from image_search_kernel.payload_schema import FIELD_PATH, SCHEMA_VERSION

    src, tgt = qdrant_in_memory_pair
    _seed_v0_collection(src, "v0_vectors")

    # Capture original vectors by path.
    original_points, _ = src.scroll(
        collection_name="v0_vectors", with_payload=True, with_vectors=True, limit=100,
    )
    original_vectors = {
        p.payload[FIELD_PATH]: p.vector for p in original_points
    }

    report = migrate_collection(
        source=src, target=tgt,
        source_collection="v0_vectors", target_collection="v1_vectors",
        target_version=SCHEMA_VERSION,
        model_meta=ModelMeta(name="test", dim=4, revision="r0"),
        field_transforms=make_v0_to_v1_transforms(),
        vector_strategy="copy",
    )
    assert report.total_failed == 0

    new_points, _ = tgt.scroll(
        collection_name="v1_vectors", with_payload=True, with_vectors=True, limit=100,
    )
    for p in new_points:
        original = original_vectors[p.payload[FIELD_PATH]]
        assert p.vector == original, "vector was modified during copy migration"


def test_migrate_unknown_version_raises(qdrant_in_memory_pair):
    """Refusal: target_version outside the known-good set is rejected."""
    from image_search_kernel.migrate import (
        ModelMeta,
        make_v0_to_v1_transforms,
        migrate_collection,
    )
    from image_search_kernel.payload_schema import require_fields

    src, tgt = qdrant_in_memory_pair
    _seed_v0_collection(src, "v0_bad")

    # String form ("v99") is accepted, parsed, then refused by
    # migrate_collection (only SCHEMA_VERSION is supported).
    with pytest.raises(ValueError, match="not in the known-good set"):
        migrate_collection(
            source=src, target=tgt,
            source_collection="v0_bad", target_collection="v1_bad",
            target_version="v99",
            model_meta=ModelMeta(name="test", dim=4, revision="r0"),
            field_transforms=make_v0_to_v1_transforms(),
        )

    # Direct call to require_fields rejects unknown version numbers.
    with pytest.raises(ValueError, match="unknown schema version"):
        require_fields({}, version=99)


def test_migrate_reports_failures_does_not_raise(qdrant_in_memory_pair):
    """Migration failures are surfaced in the report, not raised.

    A point whose payload triggers an exception during transform
    does not abort the whole migration — its id is recorded in
    `failures` and the run continues.
    """
    from image_search_kernel.migrate import (
        FieldTransform,
        ModelMeta,
        migrate_collection,
    )

    src, tgt = qdrant_in_memory_pair
    ids = _seed_v0_collection(src, "v0_fail")

    def explode_on_bad_payload(old_payload: dict, _meta: ModelMeta) -> str:
        # Fail on a recognizable sentinel payload (real dict, but
        # marked so the transform blows up).
        if old_payload.get("path") == "BAD":
            raise ValueError("intentional failure for test")
        from pathlib import PurePosixPath
        return str(PurePosixPath(old_payload.get("path", "")).parent)

    transforms: dict[str, FieldTransform] = {"folder": explode_on_bad_payload}

    # Inject a sentinel point that will trigger the failure.
    bad_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "bad-point"))
    src.upsert(
        collection_name="v0_fail",
        points=[qmodels.PointStruct(
            id=bad_id,
            vector=[0.0, 0.0, 1.0, 0.0],
            payload={"id": bad_id, "path": "BAD", "blurhash": None},
        )],
    )

    report = migrate_collection(
        source=src, target=tgt,
        source_collection="v0_fail", target_collection="v1_fail",
        target_version=1,
        model_meta=ModelMeta(name="test", dim=4, revision="r0"),
        field_transforms=transforms,
    )

    # The migration completed (didn't raise), even though one point failed.
    assert report.total_failed >= 1
    assert any(fid == bad_id for fid, _ in report.failures)


def test_migrate_progress_callback(qdrant_in_memory_pair):
    """The `on_progress` callback is invoked at least once."""
    from image_search_kernel.migrate import (
        MigrationProgress,
        ModelMeta,
        make_v0_to_v1_transforms,
        migrate_collection,
    )
    from image_search_kernel.payload_schema import SCHEMA_VERSION

    src, tgt = qdrant_in_memory_pair
    _seed_v0_collection(src, "v0_progress")

    calls: list[MigrationProgress] = []
    migrate_collection(
        source=src, target=tgt,
        source_collection="v0_progress", target_collection="v1_progress",
        target_version=SCHEMA_VERSION,
        model_meta=ModelMeta(name="test", dim=4, revision="r0"),
        field_transforms=make_v0_to_v1_transforms(),
        on_progress=lambda p: calls.append(p),
    )
    assert calls, "on_progress was not invoked"
    assert all(isinstance(c, MigrationProgress) for c in calls)


def test_migrate_elapsed_seconds_is_positive(qdrant_in_memory_pair):
    """The report's `elapsed_seconds` field is a non-negative float."""
    from image_search_kernel.migrate import (
        ModelMeta,
        make_v0_to_v1_transforms,
        migrate_collection,
    )
    from image_search_kernel.payload_schema import SCHEMA_VERSION

    src, tgt = qdrant_in_memory_pair
    _seed_v0_collection(src, "v0_elapsed")
    report = migrate_collection(
        source=src, target=tgt,
        source_collection="v0_elapsed", target_collection="v1_elapsed",
        target_version=SCHEMA_VERSION,
        model_meta=ModelMeta(name="test", dim=4, revision="r0"),
        field_transforms=make_v0_to_v1_transforms(),
    )
    assert report.elapsed_seconds >= 0.0
    assert isinstance(report.elapsed_seconds, float)
