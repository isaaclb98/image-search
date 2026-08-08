"""tests/test_schema.py — schema invariants.

Pins the payload field set so a drift between `indexer/schema.py`,
`indexer/upsert.py:build_payload`, and `SCHEMA.md` shows up as a
test failure rather than as a silent search-side bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def schema_module():
    from indexer import schema
    return schema


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
    """Cross-check: every FIELD_* constant in schema.py must appear in
    the dict produced by build_payload(). Catches the case where a
    field gets added to the schema but not to the writer."""
    from PIL import Image

    from indexer.schema import payload_field_names
    from indexer.upsert import build_payload

    img = Image.new("RGB", (16, 16), color=(10, 20, 30))
    p = tmp_path / "sample.png"
    img.save(p, "PNG")

    payload = build_payload(
        p, shard="", model_name="test-model", model_revision="r0",
        collection="default",
    )

    expected = set(payload_field_names())
    actual = set(payload.keys())
    missing = expected - actual
    assert not missing, f"build_payload missing fields: {sorted(missing)}"


def test_schema_doc_lists_every_payload_field(schema_module):
    """Cross-check: SCHEMA.md must mention every payload field name.
    Catches drift between the schema module and the prose doc."""
    repo_root = Path(__file__).resolve().parent.parent
    doc = (repo_root / "SCHEMA.md").read_text(encoding="utf-8")
    for name in schema_module.payload_field_names():
        assert f"`{name}`" in doc, f"SCHEMA.md missing field `{name}`"
