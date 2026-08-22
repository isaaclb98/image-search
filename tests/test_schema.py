"""tests/test_schema.py — schema invariants.

Pins the payload field set so a drift between
`image_search_kernel.payload_schema`, `indexer/upsert.py:build_payload`,
and `SCHEMA.md` shows up as a test failure rather than as a silent
search-side bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def schema_module():
    # The kernel package owns the canonical schema constants. Tests
    # import only via this fixture; the regression test in §A1 enforces
    # no file outside `image_search_kernel/` references payload field
    # names directly.
    from image_search_kernel import payload_schema

    return payload_schema


def test_payload_field_names_are_distinct(schema_module):
    names = schema_module.payload_field_names()
    assert len(names) == len(set(names)), "duplicate field name in schema"


def test_payload_field_names_are_nonempty_strings(schema_module):
    for name in schema_module.payload_field_names():
        assert isinstance(name, str)
        assert name, "field name must not be empty"


def test_fingerprint_fields_are_in_payload(schema_module):
    payload_names = set(schema_module.payload_field_names())
    for fp in schema_module.FINGERPRINT_FIELDS:
        assert fp in payload_names, f"{fp} missing from payload_field_names()"


def test_build_payload_uses_every_schema_field(tmp_path, schema_module):
    """Cross-check: every FIELD_* constant must appear in the dict
    produced by `build_payload()`. Catches the case where a field
    gets added to the schema but not to the writer.
    """
    from PIL import Image

    from image_search_kernel.payload_schema import payload_field_names
    from indexer.upsert import build_payload

    img = Image.new("RGB", (16, 16), color=(10, 20, 30))
    p = tmp_path / "sample.png"
    img.save(p, "PNG")

    payload = build_payload(
        p, shard="", model_name="test", model_revision="r0",
        collection="default",
    )

    expected = set(payload_field_names())
    actual = set(payload.keys())
    missing = expected - actual
    assert not missing, f"build_payload missing fields: {sorted(missing)}"


def test_schema_doc_lists_every_payload_field(schema_module):
    """Cross-check: SCHEMA.md must mention every payload field name.
    Catches drift between the schema module and the prose doc.
    """
    repo_root = Path(__file__).resolve().parent.parent
    doc = (repo_root / "SCHEMA.md").read_text(encoding="utf-8")
    for name in schema_module.payload_field_names():
        assert f"`{name}`" in doc, f"SCHEMA.md missing field `{name}`"


def test_schema_version_is_positive_int(schema_module):
    """Cross-check: SCHEMA_VERSION is a positive integer.

    Readers refuse unknown versions; the writer must set a value in
    the known-good set, which starts at 1.
    """
    assert isinstance(schema_module.SCHEMA_VERSION, int)
    assert schema_module.SCHEMA_VERSION >= 1


def test_build_payload_sets_schema_version(tmp_path, schema_module):
    """Cross-check: `build_payload` writes the current `SCHEMA_VERSION`."""
    from PIL import Image

    from image_search_kernel.payload_schema import FIELD_SCHEMA_VERSION
    from indexer.upsert import build_payload

    img = Image.new("RGB", (16, 16), color=(10, 20, 30))
    p = tmp_path / "sample.png"
    img.save(p, "PNG")

    payload = build_payload(
        p, shard="", model_name="test", model_revision="r0",
        collection="default",
    )
    assert payload[FIELD_SCHEMA_VERSION] == schema_module.SCHEMA_VERSION


def test_build_payload_sets_folder_field(tmp_path, schema_module):
    """Cross-check: `build_payload` writes the `folder` field as the
    absolute parent directory path."""
    from PIL import Image

    from image_search_kernel.payload_schema import FIELD_FOLDER
    from indexer.upsert import build_payload

    img = Image.new("RGB", (16, 16), color=(10, 20, 30))
    sub = tmp_path / "vacation_photos"
    sub.mkdir()
    p = sub / "sample.png"
    img.save(p, "PNG")

    payload = build_payload(
        p, shard="", model_name="test", model_revision="r0",
        collection="default",
    )
    assert payload[FIELD_FOLDER] == str(sub.resolve())


def test_build_payload_sets_model_dim_from_registry(tmp_path, schema_module):
    """Cross-check: `build_payload` writes `model_dim` sourced from
    the registry, not from a hardcoded constant.
    """
    from PIL import Image

    from image_search_kernel.payload_schema import FIELD_MODEL_DIM
    from image_search_kernel.registry import get as registry_get
    from indexer.upsert import build_payload

    img = Image.new("RGB", (16, 16), color=(10, 20, 30))
    p = tmp_path / "sample.png"
    img.save(p, "PNG")

    payload = build_payload(
        p, shard="", model_name="test", model_revision="r0",
        collection="default",
    )
    assert payload[FIELD_MODEL_DIM] == registry_get("test").dim


def test_require_fields_v0_accepts_legacy_payload(schema_module):
    """Legacy pre-versioned points need only `id` and `path`."""
    payload = {"id": "abc", "path": "/some/where.jpg"}
    schema_module.require_fields(payload, version=0)  # does not raise


def test_require_fields_v1_rejects_missing_folder(schema_module):
    """Version-1 points must have `folder` and `model_dim`."""
    payload = {
        "_schema_version": 1,
        "id": "abc",
        "path": "/some/where.jpg",
        "model_name": "test",
        "model_revision": "r0",
        "model_dim": 1536,
        # folder missing
    }
    with pytest.raises(ValueError, match="missing required field"):
        schema_module.require_fields(payload, version=1)


def test_require_fields_unknown_version_raises(schema_module):
    with pytest.raises(ValueError, match="unknown schema version"):
        schema_module.require_fields({}, version=99)
