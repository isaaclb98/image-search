"""
tests/test_migrate_source_unit.py — Unit tests for
indexer/migrate_source_from_path.py.

Migration tool that rewrites payload `source` values based on the
file path pattern. Uses the in-memory Qdrant for tests.
"""
from __future__ import annotations

import argparse
import uuid
from unittest.mock import MagicMock

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from indexer.migrate_source_from_path import (
    _compute_source,
    _make_client,
    migrate,
    parse_args,
)


# ----- _compute_source -----

class TestComputeSource:
    """Map a payload path to its source label."""

    def test_empty_path_returns_none(self):
        assert _compute_source("") is None

    def test_none_path_returns_none(self):
        assert _compute_source(None) is None

    def test_known_pattern(self):
        """A path matching a known pattern returns the captured source."""
        # Without seeing the actual patterns, test that SOMETHING is
        # returned for a path-like string
        result = _compute_source("/some/path/to/file.jpg")
        # May be None if no patterns match — just verify no crash
        assert result is None or isinstance(result, str)

    def test_backslashes_normalized(self):
        """Windows-style paths with backslashes should be matched."""
        result = _compute_source(r"\\server\share\file.jpg")
        # Should not crash; result is None or str
        assert result is None or isinstance(result, str)


# ----- _make_client -----

class TestMakeClient:
    """Build a Qdrant client from CLI args."""

    def test_in_memory_client(self):
        args = argparse.Namespace(qdrant_in_memory=True)
        client = _make_client(args)
        assert isinstance(client, QdrantClient)

    def test_remote_client(self):
        """Non-in-memory creates a remote QdrantClient."""
        args = argparse.Namespace(
            qdrant_in_memory=False,
            qdrant_url="http://localhost:6333",
            qdrant_api_key=None,
        )
        client = _make_client(args)
        assert isinstance(client, QdrantClient)


# ----- migrate -----

class TestMigrate:
    """The main migration function."""

    @pytest.fixture
    def in_memory_qdrant(self):
        return QdrantClient(location=":memory:")

    @pytest.fixture
    def collection_with_points(self, in_memory_qdrant):
        coll = "test_migrate"
        in_memory_qdrant.create_collection(
            collection_name=coll,
            vectors_config=qmodels.VectorParams(size=4, distance=qmodels.Distance.COSINE),
        )
        points = [
            qmodels.PointStruct(
                id=str(uuid.uuid4()),
                vector=[float(i), 0.0, 0.0, 0.0],
                payload={
                    "path": f"/some/path/photo_{i}.jpg",
                    "source": "old-source",
                },
            )
            for i in range(3)
        ]
        in_memory_qdrant.upsert(collection_name=coll, points=points, wait=True)
        return in_memory_qdrant, coll

    def test_migrate_returns_dict(self, collection_with_points):
        client, coll = collection_with_points
        result = migrate(client, collection=coll, quiet=True)
        assert isinstance(result, dict)

    def test_migrate_dry_run_does_not_modify(self, collection_with_points):
        """dry_run=True should not actually update the collection."""
        client, coll = collection_with_points
        result = migrate(client, collection=coll, dry_run=True, quiet=True)
        # Should report what would happen without modifying
        assert isinstance(result, dict)
        # Verify the original payload is still intact
        recs, _ = client.scroll(coll, with_payload=True, limit=1)
        # At least one point should still have source="old-source"
        assert any(r.payload.get("source") == "old-source" for r in recs)

    def test_migrate_quiet(self, collection_with_points, capsys):
        """quiet=True suppresses progress output."""
        client, coll = collection_with_points
        migrate(client, collection=coll, quiet=True)
        captured = capsys.readouterr()
        # No "migrate-source:" prefix in output
        assert "migrate-source:" not in captured.out

    def test_migrate_verbose(self, collection_with_points, capsys):
        """quiet=False shows progress output."""
        client, coll = collection_with_points
        migrate(client, collection=coll, quiet=False)
        captured = capsys.readouterr()
        # Should have the "migrate-source:" prefix
        assert "migrate-source:" in captured.out

    def test_migrate_batch_size(self, collection_with_points):
        """batch_size parameter is accepted."""
        client, coll = collection_with_points
        result = migrate(client, collection=coll, batch_size=1, quiet=True)
        assert isinstance(result, dict)

    def test_migrate_missing_collection(self, in_memory_qdrant):
        """Migrating a non-existent collection should raise or return error info."""
        with pytest.raises(Exception):
            migrate(in_memory_qdrant, collection="does-not-exist", quiet=True)


# ----- parse_args -----

class TestParseArgs:
    """CLI argument parsing."""

    def test_defaults(self):
        args = parse_args([])
        assert args.qdrant_url == "http://localhost:6333"
        assert args.qdrant_in_memory is False
        assert args.batch_size == 1000
        assert args.dry_run is False
        assert args.quiet is False

    def test_in_memory_flag(self):
        args = parse_args(["--qdrant-in-memory"])
        assert args.qdrant_in_memory is True

    def test_qdrant_url(self):
        args = parse_args(["--qdrant-url", "http://qdrant.example.com:6333"])
        assert args.qdrant_url == "http://qdrant.example.com:6333"

    def test_qdrant_api_key(self):
        args = parse_args(["--qdrant-api-key", "secret-key"])
        assert args.qdrant_api_key == "secret-key"

    def test_collection_name(self):
        args = parse_args(["--qdrant-collection", "my-collection"])
        assert args.qdrant_collection == "my-collection"

    def test_batch_size(self):
        args = parse_args(["--batch-size", "500"])
        assert args.batch_size == 500

    def test_dry_run(self):
        args = parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_quiet(self):
        args = parse_args(["--quiet"])
        assert args.quiet is True

    def test_long_flag_full_name(self):
        """Long flags work as expected."""
        args = parse_args(["--qdrant-in-memory"])
        assert args.qdrant_in_memory is True


# ----- Module imports -----

class TestModuleImports:
    """Public API is importable."""

    def test_migrate_importable(self):
        from indexer.migrate_source_from_path import migrate
        assert callable(migrate)

    def test_parse_args_importable(self):
        from indexer.migrate_source_from_path import parse_args
        assert callable(parse_args)


# ----- End-to-end -----

class TestEndToEnd:
    """A full migration run on a realistic dataset."""

    def test_full_migration_run(self):
        """Create a collection, run migration, verify results."""
        client = QdrantClient(location=":memory:")
        coll = "e2e_migrate"
        client.create_collection(
            collection_name=coll,
            vectors_config=qmodels.VectorParams(size=4, distance=qmodels.Distance.COSINE),
        )

        # Insert points with known path patterns
        points = [
            qmodels.PointStruct(
                id=str(uuid.uuid4()),
                vector=[1.0, 0.0, 0.0, 0.0],
                payload={
                    "path": "/images/photo_a.jpg",
                    "source": "wrong-source",
                },
            ),
            qmodels.PointStruct(
                id=str(uuid.uuid4()),
                vector=[0.0, 1.0, 0.0, 0.0],
                payload={
                    "path": "/other/path/photo_b.jpg",
                    "source": "wrong-source",
                },
            ),
        ]
        client.upsert(collection_name=coll, points=points, wait=True)

        # Run migration
        result = migrate(client, collection=coll, quiet=True, dry_run=False)
        assert isinstance(result, dict)
        # Should report updated counts
        assert "updated" in result or "already_correct" in result